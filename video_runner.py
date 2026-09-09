"""Run the installed LTX pipeline and preserve actual peak MLX memory."""
import json
from pathlib import Path
import sys
import mlx.core as mx
from ltx_pipelines_mlx.cli import main
from ltx_core_mlx.text_encoders.gemma import feature_extractor
from ltx_core_mlx.loader import fuse_loras

# The upstream connector only splits its large lazy graphs on <=48 GB Macs.
# The shared 96 GB Studio also hits the Metal watchdog without these boundaries.
# This changes evaluation scheduling, not weights, precision or model arithmetic.
feature_extractor._materialize = mx.eval

# LoRA fusion otherwise stays lazy until the first refinement block. Evaluate
# each fused weight separately so delta/quantization graphs cannot accumulate
# across the entire 22B model and trip the watchdog during refinement.
_fuse_deltas = fuse_loras._fuse_deltas
def materialized_fusion(*args, **kwargs):
    tensors = _fuse_deltas(*args, **kwargs)
    mx.eval(*tensors.values())
    return tensors
fuse_loras._fuse_deltas = materialized_fusion
mx.set_cache_limit(2 * 2**30)

if __name__=='__main__':
    destination=Path(sys.argv[sys.argv.index('-o')+1]).parent
    main()
    (destination/'video-metrics.json').write_text(json.dumps({'peak_memory_gib':round(mx.get_peak_memory()/2**30,3)}))
