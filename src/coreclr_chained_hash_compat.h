#ifndef DNVM_CORECLR_CHAINED_HASH_COMPAT_H
#define DNVM_CORECLR_CHAINED_HASH_COMPAT_H

#include "coreclr_compat.h"

using ULONG = std::uint32_t;
using PTR_ULONG = ULONG*;
#ifdef _DEBUG
#define INDEBUG(expression) expression
#else
#define INDEBUG(expression)
#endif
#define DAC_ALIGNAS(type)

inline bool FAILED(HRESULT result) { return result < 0; }
inline HRESULT OutOfMemory() { return E_OUTOFMEMORY; }

// The extracted collection needs only unsigned checked addition and multiplication.
template <typename T>
class CoreClrCheckedUnsigned
{
public:
    explicit CoreClrCheckedUnsigned(T value) : value_(value) {}
    bool IsOverflow() const { return overflow_; }
    T Value() const
    {
        if (overflow_)
            throw std::overflow_error("CoreCLR checked integer overflow");
        return value_;
    }
    CoreClrCheckedUnsigned operator+(CoreClrCheckedUnsigned other) const
    {
        CoreClrCheckedUnsigned result(0);
        result.overflow_ = overflow_ || other.overflow_ ||
            other.value_ > std::numeric_limits<T>::max() - value_;
        if (!result.overflow_)
            result.value_ = value_ + other.value_;
        return result;
    }
    CoreClrCheckedUnsigned operator*(CoreClrCheckedUnsigned other) const
    {
        CoreClrCheckedUnsigned result(0);
        result.overflow_ = overflow_ || other.overflow_ ||
            (value_ != 0 && other.value_ > std::numeric_limits<T>::max() / value_);
        if (!result.overflow_)
            result.value_ = value_ * other.value_;
        return result;
    }

private:
    T value_;
    bool overflow_ = false;
};

using S_SIZE_T = CoreClrCheckedUnsigned<std::size_t>;
using S_UINT32 = CoreClrCheckedUnsigned<std::uint32_t>;

#endif
