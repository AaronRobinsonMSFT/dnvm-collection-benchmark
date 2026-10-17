#include "dn-simdhash.h"
#include "dn-simdhash-utils.h"
#include "dn-simdhash-arch.h"
#include "runtime_platform.h"

#ifdef DN_SIMDHASH_USE_SCALAR_FALLBACK
#error "This benchmark requires the runtime collection's SIMD implementation"
#endif

const char* dnvm_runtime_simd_path(void)
{
#if DN_SIMDHASH_USE_SSE2
    return "sse2";
#elif defined(__ARM_ARCH_ISA_A64)
    return "neon";
#else
#error "Record the SIMD path before enabling another architecture"
#endif
}
