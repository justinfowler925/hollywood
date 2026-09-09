// Fixed camera ocean compositor. No frame sequence is written to disk.
import Foundation
import Metal
import MetalKit
import AVFoundation
import CoreVideo

func require(_ ok: Bool, _ message: String) throws { if !ok { throw NSError(domain: "OceanRenderer", code: 1, userInfo: [NSLocalizedDescriptionKey: message]) } }
let args = CommandLine.arguments
func main() throws {
    try require(args.count == 8, "Usage: renderer image output width height seconds horizon birds")
    let width = Int(args[3])!, height = Int(args[4])!, seconds = Double(args[5])!, horizon = Float(args[6])!, birds = Float(args[7])!
    try require([1920,3840].contains(width) && height == width * 9 / 16 && seconds >= 1 && seconds <= 256, "Invalid render dimensions/duration")
    let started = Date()
    guard let device = MTLCreateSystemDefaultDevice(), let queue = device.makeCommandQueue() else { throw NSError(domain: "Metal unavailable", code: 1) }
    let shaderURL = URL(fileURLWithPath: args[0]).deletingLastPathComponent().appendingPathComponent("ocean.metal")
    let library = try device.makeLibrary(source: String(contentsOf: shaderURL, encoding: .utf8), options: nil)
    let pipeline = try device.makeComputePipelineState(function: library.makeFunction(name: "ocean")!)
    let texture = try MTKTextureLoader(device: device).newTexture(URL: URL(fileURLWithPath: args[1]), options: [.SRGB:false, .origin:MTKTextureLoader.Origin.topLeft])
    let url = URL(fileURLWithPath: args[2]); try? FileManager.default.removeItem(at: url)
    let writer = try AVAssetWriter(outputURL: url, fileType: .mp4)
    let settings: [String:Any] = [AVVideoCodecKey:AVVideoCodecType.h264,AVVideoWidthKey:width,AVVideoHeightKey:height,AVVideoCompressionPropertiesKey:[AVVideoAverageBitRateKey:width == 3840 ? 16000000 : 6000000, AVVideoMaxKeyFrameIntervalKey:48, AVVideoAllowFrameReorderingKey:false, AVVideoProfileLevelKey:AVVideoProfileLevelH264HighAutoLevel], AVVideoColorPropertiesKey:[AVVideoColorPrimariesKey:AVVideoColorPrimaries_ITU_R_709_2,AVVideoTransferFunctionKey:AVVideoTransferFunction_ITU_R_709_2,AVVideoYCbCrMatrixKey:AVVideoYCbCrMatrix_ITU_R_709_2]]
    let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
    input.expectsMediaDataInRealTime = false
    let adapter = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [kCVPixelBufferPixelFormatTypeKey as String:kCVPixelFormatType_32BGRA,kCVPixelBufferWidthKey as String:width,kCVPixelBufferHeightKey as String:height,kCVPixelBufferMetalCompatibilityKey as String:true,kCVPixelBufferIOSurfacePropertiesKey as String:[:]])
    writer.add(input); try require(writer.startWriting(), "Cannot start encoder: \(String(describing:writer.error))")
    writer.startSession(atSourceTime: .zero)
    var cache: CVMetalTextureCache?; try require(CVMetalTextureCacheCreate(nil,nil,device,nil,&cache) == kCVReturnSuccess, "Texture cache unavailable")
    let frames = Int((seconds * 24).rounded())
    // Discard four seconds of encoder startup so each retained boundary has steady quality.
    let warmup = 96
    for frame in 0..<(frames + warmup) {
        try autoreleasepool {
            let waitStart = Date()
            while !input.isReadyForMoreMediaData {
                try require(writer.status == .writing && Date().timeIntervalSince(waitStart) < 30, "Encoder stalled: \(String(describing:writer.error))")
                Thread.sleep(forTimeInterval: 0.002)
            }
            var buffer: CVPixelBuffer?
            try require(CVPixelBufferPoolCreatePixelBuffer(nil,adapter.pixelBufferPool!,&buffer) == kCVReturnSuccess, "Pixel buffer allocation failed")
            var cvTexture: CVMetalTexture?
            try require(CVMetalTextureCacheCreateTextureFromImage(nil,cache!,buffer!,nil,.bgra8Unorm,width,height,0,&cvTexture) == kCVReturnSuccess, "Output texture failed")
            let command = queue.makeCommandBuffer()!, encoder = command.makeComputeCommandEncoder()!
            // Integer-frequency phases make t=duration identical to t=0.
            var uniforms = SIMD4<Float>(Float(frame-warmup)/Float(frames),horizon,birds,Float(width)/Float(height))
            encoder.setComputePipelineState(pipeline);encoder.setTexture(texture,index:0);encoder.setTexture(CVMetalTextureGetTexture(cvTexture!)!,index:1)
            encoder.setBytes(&uniforms,length:MemoryLayout<SIMD4<Float>>.size,index:0)
            encoder.dispatchThreads(MTLSize(width:width,height:height,depth:1),threadsPerThreadgroup:MTLSize(width:16,height:16,depth:1));encoder.endEncoding()
            command.commit();command.waitUntilCompleted()
            try require(command.status == .completed, "GPU render failed")
            try require(adapter.append(buffer!,withPresentationTime:CMTime(value:Int64(frame),timescale:24)), "Frame append failed: \(String(describing:writer.error))")
        }
        if frame % 240 == 0 { print("Rendered \(frame)/\(frames)");fflush(stdout) }
    }
    input.markAsFinished();writer.endSession(atSourceTime:CMTime(value:Int64(frames+warmup),timescale:24))
    let done=DispatchSemaphore(value:0);writer.finishWriting {done.signal()};done.wait()
    try require(writer.status == .completed, "Encoder failed: \(String(describing:writer.error))")
    print("METRICS " + String(data:try JSONSerialization.data(withJSONObject:["frames":frames,"width":width,"height":height,"render_seconds":Date().timeIntervalSince(started),"device":device.name]),encoding:.utf8)!)
}
do {try main()} catch {fputs("\(error)\n",stderr);exit(1)}
