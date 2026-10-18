#ifndef DNVM_CORECLR_COMPAT_H
#define DNVM_CORECLR_COMPAT_H

#include <cassert>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <exception>
#include <limits>
#include <new>
#include <stdexcept>
#include <type_traits>
#include <utility>
#include "minipal/utils.h"

// Isolate the pinned collection bodies from VM platform, contract and DAC
// infrastructure. Collection and iterator algorithms remain unchanged.
#define __UtilCode_h__
#define CLRTYPES_H_
#define CHECK_H_
#define CONTRACT_H_
#define __daccess_h__
#define _EX_H_
#define __HOLDER_H_
#define _stdmacros_h_
#define DPTR(Type) Type*
#define SPTR(Type) Type*
#define ArrayDPTR(Type) Type*
#ifdef _MSC_VER
#define DECLSPEC_NORETURN __declspec(noreturn)
#else
#define DECLSPEC_NORETURN __attribute__((noreturn))
#endif
#define LIMITED_METHOD_CONTRACT
#define LIMITED_METHOD_DAC_CONTRACT
#define WRAPPER_NO_CONTRACT
#define SUPPORTS_DAC
#define SUPPORTS_DAC_WRAPPER
#define SUPPORTS_DAC_HOST_ONLY
#define STATIC_CONTRACT_NOTHROW
#define STATIC_CONTRACT_CANNOT_TAKE_LOCK
#define CONTRACTL
#define CONTRACTL_END
#define NOTHROW
#define THROWS
#define GC_NOTRIGGER
#define INSTANCE_CHECK
#define DESTRUCTOR_CHECK
#define CANNOT_TAKE_LOCK
#define DEBUG_ONLY
#define CONTRACT_CHECK
#define CONTRACT_CHECK_END
#define CONTRACT_VIOLATION(violation)
#define CONSISTENCY_CHECK(condition) assert(condition)
#define PRECONDITION(condition) assert(condition)
#define PRECONDITION_MSG(condition, message) assert(condition)
#define _Out_writes_bytes_(size)
#define _ASSERTE(condition) assert(condition)
#define CHECK_OK return true
#define CHECK(condition) do { if (!(condition)) return false; } while (false)
#define CHECK_MSG(condition, message) CHECK(condition)
#define DEFINE_MEMBER_EXISTENCE_CHECK(Member) \
    template <typename T, typename = void> struct has_##Member : std::false_type {}; \
    template <typename T> struct has_##Member<T, std::void_t<decltype(std::declval<T>().Member)>> \
        : std::true_type {}

using BOOL = int;
using COUNT_T = std::uint32_t;
using SCOUNT_T = std::int32_t;
using TADDR = std::uintptr_t;
using CHAR = char;
using WCHAR = char16_t;
using BYTE = unsigned char;
using DWORD = std::uint32_t;
using UINT32 = std::uint32_t;
using UINT64 = std::uint64_t;
using INT = int;
using SIZE_T = std::size_t;
using PTR_VOID = void*;
using HRESULT = std::int32_t;
using CHECK = bool;
using std::nothrow;
constexpr BOOL TRUE = 1;
constexpr BOOL FALSE = 0;
constexpr bool NULL_OK = true;
constexpr bool NULL_NOT_OK = false;
constexpr HRESULT S_OK = 0;
constexpr HRESULT E_OUTOFMEMORY = static_cast<HRESULT>(0x8007000eu);
constexpr HRESULT COR_E_OVERFLOW = static_cast<HRESULT>(0x80131516u);
using std::min;
using std::max;

template <typename T, typename U> T dac_cast(U value) { return reinterpret_cast<T>(value); }
template <typename T, typename U>
bool FitsIn(U value)
{
    if constexpr (std::is_signed_v<U>)
    {
        if (value < 0)
        {
            if constexpr (std::is_signed_v<T>)
                return static_cast<std::intmax_t>(value) >= std::numeric_limits<T>::min();
            return false;
        }
    }
    return static_cast<std::uintmax_t>(value) <= static_cast<std::uintmax_t>(std::numeric_limits<T>::max());
}
inline SIZE_T AlignmentPad(SIZE_T value, SIZE_T alignment) { return (-value) & (alignment - 1); }
inline SIZE_T AlignmentTrim(SIZE_T value, SIZE_T alignment) { return value & (alignment - 1); }
inline void MoveMemory(void* to, const void* from, SIZE_T size) { std::memmove(to, from, size); }
inline void ZeroMemory(void* to, SIZE_T size) { std::memset(to, 0, size); }
[[noreturn]] inline void ThrowHR(HRESULT error)
{
    if (error == E_OUTOFMEMORY)
        throw std::bad_alloc();
    throw std::overflow_error("CoreCLR buffer size overflow");
}
class noncopyable
{
protected:
    noncopyable() = default;
    ~noncopyable() = default;
private:
    noncopyable(const noncopyable&) = delete;
    noncopyable& operator=(const noncopyable&) = delete;
};

// Unused string specializations must still parse; these are not linked or used
// by the integer-key SHash comparisons.
class SString;
std::size_t HashStringA(const char*);
std::size_t HashString(const char16_t*);
int u16_strcmp(const char16_t*, const char16_t*);
template <typename T>
bool CheckPointer(T* pointer, bool null_allowed = false) { return pointer != nullptr || null_allowed; }
[[noreturn]] inline void ThrowOutOfMemory() { throw std::bad_alloc(); }

#include "shash.h"
#include "sarray.h"
#include "arraylist.h"
#include "slist.h"

#endif
