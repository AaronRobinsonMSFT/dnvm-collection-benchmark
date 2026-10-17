#include <cstddef>
#include <cstdlib>
#include <limits>
#include <new>

#include "memory_accounting.h"

namespace
{
struct alignas(std::max_align_t) ArrayAllocation
{
    dnvm::MemoryAccount* account;
};
}

// SHash uses new[] directly rather than an allocator trait. Only the untimed
// memory/test executables link these replacements; timing uses normal new[].
void* operator new[](std::size_t bytes)
{
    if (bytes > std::numeric_limits<std::size_t>::max() - sizeof(ArrayAllocation))
        throw std::bad_array_new_length();
    void* allocation = std::malloc(sizeof(ArrayAllocation) + bytes);
    if (allocation == nullptr)
        throw std::bad_alloc();
    auto* header = new (allocation) ArrayAllocation{dnvm::MemoryScope::active()};
    void* pointer = header + 1;
    try
    {
        if (header->account != nullptr)
            header->account->record(pointer, bytes);
    }
    catch (...)
    {
        std::free(allocation);
        throw;
    }
    return pointer;
}

void operator delete[](void* pointer) noexcept
{
    if (pointer == nullptr)
        return;
    auto* header = static_cast<ArrayAllocation*>(pointer) - 1;
    if (header->account != nullptr)
        header->account->release(pointer);
    std::free(header);
}

void operator delete[](void* pointer, std::size_t) noexcept
{
    ::operator delete[](pointer);
}

void* operator new[](std::size_t bytes, const std::nothrow_t&) noexcept
{
    try
    {
        return ::operator new[](bytes);
    }
    catch (const std::bad_alloc&)
    {
        return nullptr;
    }
}

void operator delete[](void* pointer, const std::nothrow_t&) noexcept
{
    ::operator delete[](pointer);
}
