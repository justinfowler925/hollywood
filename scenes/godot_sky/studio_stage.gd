# Studio harness around the pinned MIT Godot sky demonstration.
extends SceneTree
var stage: Node3D
var elapsed := 0.0
var frames := 0
var started := 0
var water_material: ShaderMaterial
var visual_material: ShaderMaterial
var moving: Array[Node3D] = []
var config: Dictionary
var output_folder: String
func _initialize() -> void:
    var request_path := OS.get_environment("STUDIO_SCENE_REQUEST")
    var request: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(request_path))
    config = request["args"]
    output_folder = request["folder"]
    root.content_scale_size = Vector2i(int(config["width"]), int(config["height"]))
    root.content_scale_mode = Window.CONTENT_SCALE_MODE_VIEWPORT
    call_deferred("setup")
func setup() -> void:
    stage = load("res://Main.tscn").instantiate()
    root.add_child(stage)
    stage.set_process(false)
    stage.set_process_input(false)
    Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
    stage.get_node("Panel").hide()
    stage.get_node("Help").hide()
    stage.get_node("Spheres").hide()
    var anim: AnimationPlayer = stage.get_node("AnimationPlayer")
    anim.pause()
    anim.seek(5.0, true)
    var camera: Camera3D = stage.get_node("YawCamera/Camera3D")
    camera.position = Vector3(0, 3, 20)
    camera.rotation.x = -0.015
    camera.far = 10000
    camera.rotation.x = -0.08 if config["scene"] == "forest" else -0.015
    var sky: ShaderMaterial = stage.get_node("WorldEnvironment").environment.sky.sky_material
    sky.set_shader_parameter("cloud_coverage", 0.18)
    sky.set_shader_parameter("mie", 0.005)
    sky.set_shader_parameter("mie_color", Color(0.63,0.77,0.92,1))
    sky.set_shader_parameter("rayleigh", 2.0)
    sky.set_shader_parameter("rayleigh_color", Color(0.26,0.41,0.58,1))
    sky.set_shader_parameter("turbidity", 10.0)
    sky.set_shader_parameter("exposure", 0.25)
    sky.set_shader_parameter("cloud_density", 0.025)
    sky.set_shader_parameter("cloud_time_scale", float(config["cloud_speed"]))
    sky.set_shader_parameter("cloud_steps_range", Vector2(64,32))
    var sun: DirectionalLight3D = stage.get_node("YawLight/DirectionalLight3D")
    sun.rotation.x = -0.15 - sin(float(config["time_of_day"])/24.0*PI)*1.2
    if config["scene"] in ["abstract","music_visualizer","space","rain_window"]:
        make_visuals()
        started = Time.get_ticks_msec()
        return
    if config["scene"] in ["forest","studio"]:
        make_set()
        started = Time.get_ticks_msec()
        return
    var ocean := MeshInstance3D.new()
    var mesh := PlaneMesh.new()
    mesh.size = Vector2(400,400)
    mesh.subdivide_width = 256
    mesh.subdivide_depth = 256
    ocean.mesh = mesh
    water_material = ShaderMaterial.new()
    water_material.shader = load("res://studio_water.gdshader")
    ocean.material_override = water_material
    stage.add_child(ocean)
    water_material.set_shader_parameter("wave_height", float(config["wave_height"]))
    water_material.set_shader_parameter("tint",Vector3(config["colors"][0][0],config["colors"][0][1],config["colors"][0][2])*.18)
    var horizon := MeshInstance3D.new()
    var horizon_mesh := PlaneMesh.new()
    horizon_mesh.size = Vector2(20000,20000)
    horizon.mesh = horizon_mesh
    horizon.position.y = -6.0
    var horizon_mat := StandardMaterial3D.new()
    horizon_mat.albedo_color = Color(0.015,0.11,0.14)
    horizon_mat.roughness = 0.15
    horizon_mat.metallic = 0.25
    horizon.material_override = horizon_mat
    stage.add_child(horizon)
    started = Time.get_ticks_msec()
func _process(delta: float) -> bool:
    if stage == null: return false
    frames += 1
    elapsed = float(config["start_time"]) + (frames - 1) / 24.0
    stage.get_node("WorldEnvironment").environment.sky.sky_material.set_shader_parameter("cloud_time_offset", elapsed + float(config["seed"]) * 0.1)
    var motion_time := elapsed * float(config["intensity"])
    if water_material != null: water_material.set_shader_parameter("elapsed", motion_time)
    if visual_material != null:
        visual_material.set_shader_parameter("elapsed", motion_time)
        var envelopes: Array = config["envelope"]
        if frames <= envelopes.size():
            var sample: Array = envelopes[frames-1]
            visual_material.set_shader_parameter("energy", float(sample[0]))
            visual_material.set_shader_parameter("bands", Vector3(sample[1],sample[2],sample[3]))
    for i in range(moving.size()):
        var object := moving[i]
        if object.name.begins_with("Firefly"):
            object.position.y = 2.0 + sin(motion_time*.7+i)*1.3
            object.position.x = sin(motion_time*.3+i*2.1)*8.0
        elif config["scene"]=="forest": object.rotation.z = sin(motion_time*.5+i)*.025
        else: object.rotation.y = motion_time*.3
    var camera: Camera3D = stage.get_node("YawCamera/Camera3D")
    if config["camera"]=="drift": camera.position.x = sin(motion_time*.15)*.5
    if config["camera"]=="orbit" and config["scene"] in ["studio","forest","ocean"]:
        camera.position=Vector3(sin(motion_time*.06)*18.,5.,cos(motion_time*.06)*18.)
        camera.look_at(Vector3(0,3,0))
    if frames % 24 == 0:
        print(JSON.stringify({"frame":frames,"total":int(config["seconds"])*24}))
    if frames == int(config["seconds"]) * 24:
        var receipt := FileAccess.open(output_folder + "/render.json", FileAccess.WRITE)
        receipt.store_string(JSON.stringify({"frames":frames,"wall_seconds":(Time.get_ticks_msec()-started)/1000.0,"engine":Engine.get_version_info(),"device":RenderingServer.get_video_adapter_name(),"driver":RenderingServer.get_current_rendering_driver_name()}))
        quit()
    return false

func make_visuals() -> void:
    var layer := CanvasLayer.new()
    root.add_child(layer)
    var rect := ColorRect.new()
    rect.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
    visual_material=ShaderMaterial.new()
    visual_material.shader=load("res://studio_visuals.gdshader")
    var preset: String = config["scene"]
    visual_material.set_shader_parameter("mode",1 if preset=="space" else (2 if preset=="rain_window" else 0))
    var colors: Array = config["colors"]
    visual_material.set_shader_parameter("primary",Vector3(colors[0][0],colors[0][1],colors[0][2]))
    visual_material.set_shader_parameter("secondary",Vector3(colors[1][0],colors[1][1],colors[1][2]))
    visual_material.set_shader_parameter("camera_mode",["fixed","drift","orbit"].find(config["camera"]))
    visual_material.set_shader_parameter("intensity",float(config["intensity"]))
    visual_material.set_shader_parameter("daylight",.35+.65*sin(float(config["time_of_day"])/24.*PI))
    rect.material=visual_material
    layer.add_child(rect)

func material(color: Color, metallic: float=0.0, emission: bool=false) -> StandardMaterial3D:
    var m := StandardMaterial3D.new()
    m.albedo_color=color
    m.metallic=metallic
    m.roughness=.3 if metallic>0 else .85
    if emission:
        m.emission_enabled=true
        m.emission=color
        m.emission_energy_multiplier=3.0
    return m
func mesh_object(mesh: Mesh, position: Vector3, mat: Material) -> MeshInstance3D:
    var obj := MeshInstance3D.new()
    obj.mesh=mesh
    obj.position=position
    obj.material_override=mat
    stage.add_child(obj)
    return obj
func make_set() -> void:
    var environment: Environment=stage.get_node("WorldEnvironment").environment
    environment.glow_enabled=true
    var ground := PlaneMesh.new()
    ground.size=Vector2(250,250)
    mesh_object(ground,Vector3.ZERO,material(Color(.045,.075,.04) if config["scene"]=="forest" else Color(.018,.025,.045)))
    var rng := RandomNumberGenerator.new()
    rng.seed=int(config["seed"])
    if config["scene"]=="forest":
        environment.fog_enabled=true
        environment.fog_density=.012
        environment.fog_light_color=Color(.4,.5,.35).lerp(Color(config["colors"][0][0],config["colors"][0][1],config["colors"][0][2]),.3)
        for i in range(85):
            var x := rng.randf_range(-32,32)
            var z := rng.randf_range(-60,12)
            if abs(x)<2.6: continue
            var h := rng.randf_range(6,13)
            var trunk := CylinderMesh.new()
            trunk.top_radius=.13;trunk.bottom_radius=.32;trunk.height=h
            mesh_object(trunk,Vector3(x,h/2,z),material(Color(.16,.09,.045)))
            var crown := SphereMesh.new()
            crown.radius=rng.randf_range(1.6,3.1);crown.height=crown.radius*2.2
            crown.radial_segments=12;crown.rings=6
            var leaf := mesh_object(crown,Vector3(x,h,z),material(Color(.055,rng.randf_range(.13,.28),.07)))
            moving.append(leaf)
        for i in range(50):
            var bug := SphereMesh.new();bug.radius=.025;bug.height=.05
            var firefly := mesh_object(bug,Vector3(rng.randf_range(-8,8),2,rng.randf_range(-12,14)),material(Color(.65,1,.12),0,true))
            firefly.name="Firefly"+str(i)
            moving.append(firefly)
    else:
        environment.background_mode=Environment.BG_COLOR
        environment.background_color=Color(.012,.018,.035)
        environment.ambient_light_source=Environment.AMBIENT_SOURCE_COLOR
        environment.ambient_light_color=Color(.2,.25,.35)
        environment.ambient_light_energy=.7
        var cam: Camera3D=stage.get_node("YawCamera/Camera3D")
        cam.position=Vector3(0,3.5,9)
        cam.look_at(Vector3(0,2.4,0))
        var base := CylinderMesh.new();base.top_radius=3.;base.bottom_radius=3.2;base.height=.6
        mesh_object(base,Vector3(0,.3,0),material(Color(.055,.065,.1),.65))
        var ring := TorusMesh.new();ring.inner_radius=1.1;ring.outer_radius=1.8
        var product := mesh_object(ring,Vector3(0,3,0),material(Color(config["colors"][0][0],config["colors"][0][1],config["colors"][0][2]),.85))
        product.rotation.x=.65
        moving.append(product)
        for x in [-5,5]:
            var light := OmniLight3D.new()
            light.position=Vector3(x,5,3);light.omni_range=20;light.light_energy=4
            light.light_color=Color(.3,.6,1) if x<0 else Color(1,.4,.2)
            stage.add_child(light)
