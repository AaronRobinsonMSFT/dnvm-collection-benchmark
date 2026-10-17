#ifndef DNVM_CORECLR_COMPAT_H
#define DNVM_CORECLR_COMPAT_H

#include <cassert>
#include <cstdint>
#include <cstring>
#include <exception>
#include <new>
#include <type_traits>
#include <utility>

// Isolate the original SHash/iterator headers from the VM's platform and contract
// headers. No hash-table or iterator implementation is replaced.
#define __UtilCode_h__
#define CLRTYPES_H_
#define CHECK_H_
#define CONTRACT_H_
#define DPTR(Type) Type*
#ifdef _MSC_VER
#define EMPTY_BASES __declspec(empty_bases)
#define DECLSPEC_NORETURN __declspec(noreturn)
#else
#define EMPTY_BASES
#define DECLSPEC_NORETURN __attribute__((noreturn))
#endif
#define LIMITED_METHOD_CONTRACT
#define LIMITED_METHOD_DAC_CONTRACT
#define WRAPPER_NO_CONTRACT
#define SUPPORTS_DAC
#define SUPPORTS_DAC_WRAPPER
#define CONTRACTL
#define CONTRACTL_END
#define NOTHROW
#define THROWS
#define GC_NOTRIGGER
#define INSTANCE_CHECK
#define PRECONDITION(condition) assert(condition)
#define _ASSERTE(condition) assert(condition)
#define UNREACHABLE() std::terminate()
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
using CHECK = bool;
using std::nothrow;
constexpr BOOL TRUE = 1;
constexpr BOOL FALSE = 0;
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
template <typename T> bool CheckPointer(T* pointer) { return pointer != nullptr; }
[[noreturn]] inline void ThrowOutOfMemory() { throw std::bad_alloc(); }

#include "shash.h"

#endif
