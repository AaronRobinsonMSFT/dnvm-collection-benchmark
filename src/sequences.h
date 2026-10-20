#ifndef DNVM_SEQUENCES_H
#define DNVM_SEQUENCES_H

#include <deque>
#include <forward_list>
#include <memory>
#include <string>
#include <type_traits>
#include <vector>

#include "absl/container/inlined_vector.h"
#include "coreclr_compat.h"
#include "dataset.h"
#include "dn-vector-ptr.h"
#include "memory_accounting.h"

namespace dnvm
{
template <typename T> struct SequenceType { using type = T; };

class RuntimeVector
{
public:
    static constexpr bool intrusive = false;
    static constexpr bool supports_reserve = true;
    static constexpr std::size_t object_bytes = sizeof(dn_vector_ptr_t);

    explicit RuntimeVector(std::uint32_t capacity = 0, dn_allocator_t* allocator = nullptr)
    {
        dn_vector_ptr_custom_alloc_params_t params{};
        params.allocator = allocator;
        params.capacity = capacity;
        if (!dn_vector_ptr_custom_init(&vector_, &params))
            throw std::bad_alloc();
    }
    ~RuntimeVector() { dn_vector_ptr_dispose(&vector_); }
    RuntimeVector(const RuntimeVector&) = delete;
    RuntimeVector& operator=(const RuntimeVector&) = delete;
    void append(void* value)
    {
        if (!dn_vector_ptr_push_back(&vector_, value))
            throw std::bad_alloc();
    }
    std::uint32_t size() const { return dn_vector_ptr_size(&vector_); }
    void* get(std::uint32_t index) const { return *dn_vector_ptr_index(&vector_, index); }
    void set(std::uint32_t index, void* value) { *dn_vector_ptr_index(&vector_, index) = value; }
    void clear() { dn_vector_ptr_clear(&vector_); }
    template <typename Visitor>
    void visit(Visitor& visitor)
    {
        DN_VECTOR_PTR_FOREACH_BEGIN(void*, value, &vector_)
            visitor(value);
        DN_VECTOR_PTR_FOREACH_END;
    }

private:
    dn_vector_ptr_t vector_;
};

template <typename Array>
class RuntimeArray
{
public:
    static constexpr bool intrusive = false;
    static constexpr bool supports_reserve = true;
    static constexpr std::size_t object_bytes = sizeof(Array);

    explicit RuntimeArray(std::uint32_t capacity = 0)
    {
        if (capacity > std::numeric_limits<int>::max() / sizeof(void*))
            throw std::length_error("SArray reservation exceeds its byte-count range");
        if (capacity != 0)
            array_.Preallocate(static_cast<int>(capacity));
    }
    RuntimeArray(const RuntimeArray&) = delete;
    RuntimeArray& operator=(const RuntimeArray&) = delete;
    void append(void* value) { array_.Append(value); }
    std::uint32_t size() const { return array_.GetCount(); }
    void* get(std::uint32_t index) const { return array_[index]; }
    void set(std::uint32_t index, void* value) { array_[index] = value; }
    void clear() { array_.Clear(); }
    template <typename Visitor>
    void visit(Visitor& visitor)
    {
        for (auto it = array_.Begin(); it != array_.End(); ++it)
            visitor(*it);
    }

private:
    Array array_;
};

class RuntimeArrayList
{
public:
    static constexpr bool intrusive = false;
    static constexpr bool supports_reserve = false;
    static constexpr std::size_t object_bytes = sizeof(ArrayList);

    explicit RuntimeArrayList(std::uint32_t capacity = 0)
    {
        if (capacity != 0)
            throw std::invalid_argument("ArrayList has no reserve operation");
    }
    RuntimeArrayList(const RuntimeArrayList&) = delete;
    RuntimeArrayList& operator=(const RuntimeArrayList&) = delete;
    void append(void* value)
    {
        if (array_.Append(value) != S_OK)
            throw std::bad_alloc();
    }
    std::uint32_t size() const { return array_.GetCount(); }
    void* get(std::uint32_t index) const { return array_.Get(index); }
    void set(std::uint32_t index, void* value) { array_.Set(index, value); }
    void clear() { array_.Clear(); }
    template <typename Visitor>
    void visit(Visitor& visitor)
    {
        auto it = array_.Iterate();
        while (it.Next())
            visitor(it.GetElement());
    }

private:
    ArrayList array_;
};

template <typename Container, bool SupportsReserve = true>
class StandardSequence
{
public:
    static constexpr bool intrusive = false;
    static constexpr bool supports_reserve = SupportsReserve;
    static constexpr std::size_t object_bytes = sizeof(Container);

    explicit StandardSequence(std::uint32_t capacity = 0)
    {
        if constexpr (SupportsReserve)
        {
            if (capacity != 0)
                values_.reserve(capacity);
        }
        else if (capacity != 0)
            throw std::invalid_argument("This sequence comparison has no reserve workload");
    }
    void append(void* value) { values_.push_back(value); }
    std::size_t size() const { return values_.size(); }
    void* get(std::uint32_t index) const { return values_[index]; }
    void set(std::uint32_t index, void* value) { values_[index] = value; }
    void clear() { values_.clear(); }
    template <typename Visitor>
    void visit(Visitor& visitor) { for (void* value : values_) visitor(value); }

private:
    Container values_;
};

class RuntimeSList
{
    struct Node
    {
        Node* m_pNext;
        void* value;
    };

public:
    static constexpr bool intrusive = true;
    static constexpr std::size_t object_bytes = sizeof(SList<Node>);

    explicit RuntimeSList(const std::vector<void*>& values)
        : nodes_(new Node[values.size()])
    {
        for (std::size_t i = values.size(); i != 0; --i)
        {
            nodes_[i - 1].value = values[i - 1];
            pool_.InsertHead(&nodes_[i - 1]);
        }
    }
    bool empty() { return list_.IsEmpty(); }
    void fill()
    {
        while (auto* node = pool_.RemoveHead())
            list_.InsertHead(node);
    }
    void clear()
    {
        while (auto* node = list_.RemoveHead())
            pool_.InsertHead(node);
    }
    template <typename Visitor>
    void visit(Visitor& visitor)
    {
        for (auto* node = list_.GetHead(); node != nullptr; node = list_.GetNext(node))
            visitor(node->value);
    }

private:
    std::unique_ptr<Node[]> nodes_;
    SList<Node> pool_;
    SList<Node> list_;
};

template <typename Allocator>
class StandardSList
{
    using List = std::forward_list<void*, Allocator>;

public:
    static constexpr bool intrusive = true;
    static constexpr std::size_t object_bytes = sizeof(List);

    explicit StandardSList(const std::vector<void*>& values)
    {
        for (auto it = values.rbegin(); it != values.rend(); ++it)
            pool_.push_front(*it);
    }
    bool empty() const { return list_.empty(); }
    void fill()
    {
        while (!pool_.empty())
            list_.splice_after(list_.before_begin(), pool_, pool_.before_begin());
    }
    void clear()
    {
        while (!list_.empty())
            pool_.splice_after(pool_.before_begin(), list_, list_.before_begin());
    }
    template <typename Visitor>
    void visit(Visitor& visitor) { for (void* value : list_) visitor(value); }

private:
    List pool_;
    List list_;
};

template <typename Runtime, std::size_t InlineCount>
struct ArrayFamily
{
    using RuntimeSequence = Runtime;
    template <template <typename> class Allocator = std::allocator, typename Visitor>
    static void implementations(Visitor visitor)
    {
        using Std = StandardSequence<std::vector<void*, Allocator<void*>>>;
        using Abseil = StandardSequence<absl::InlinedVector<void*, InlineCount, Allocator<void*>>>;
        static_assert(Std::object_bytes == sizeof(std::vector<void*>), "Tracking changed vector object size");
        static_assert(Abseil::object_bytes == sizeof(absl::InlinedVector<void*, InlineCount>),
                      "Tracking changed InlinedVector object size");
        visitor("runtime", SequenceType<Runtime>{});
        visitor("std", SequenceType<Std>{});
        visitor("abseil", SequenceType<Abseil>{});
    }
};

struct VectorFamily
{
    using RuntimeSequence = RuntimeVector;
    template <template <typename> class Allocator = std::allocator, typename Visitor>
    static void implementations(Visitor visitor)
    {
        using Runtime = std::conditional_t<std::is_same_v<Allocator<void*>, std::allocator<void*>>,
            RuntimeVector, CountedRuntimeAdapter<RuntimeVector>>;
        static_assert(sizeof(Runtime) == sizeof(RuntimeVector), "Tracking changed vector object size");
        ArrayFamily<Runtime, 8>::template implementations<Allocator>(visitor);
    }
};

struct ArrayListFamily
{
    using RuntimeSequence = RuntimeArrayList;
    template <template <typename> class Allocator = std::allocator, typename Visitor>
    static void implementations(Visitor visitor)
    {
        using Std = StandardSequence<std::vector<void*, Allocator<void*>>, false>;
        using Deque = StandardSequence<std::deque<void*, Allocator<void*>>, false>;
        using Abseil = StandardSequence<absl::InlinedVector<void*, 5, Allocator<void*>>, false>;
        static_assert(Std::object_bytes == sizeof(std::vector<void*>), "Tracking changed vector object size");
        static_assert(Deque::object_bytes == sizeof(std::deque<void*>), "Tracking changed deque object size");
        static_assert(Abseil::object_bytes == sizeof(absl::InlinedVector<void*, 5>),
                      "Tracking changed InlinedVector object size");
        visitor("runtime", SequenceType<RuntimeArrayList>{});
        visitor("std", SequenceType<Std>{});
        visitor("std_deque", SequenceType<Deque>{});
        visitor("abseil", SequenceType<Abseil>{});
    }
};

struct SListFamily
{
    using RuntimeSequence = RuntimeSList;
    template <template <typename> class Allocator = std::allocator, typename Visitor>
    static void implementations(Visitor visitor)
    {
        using Std = StandardSList<Allocator<void*>>;
        static_assert(Std::object_bytes == sizeof(std::forward_list<void*>),
                      "Tracking changed forward_list object size");
        visitor("runtime", SequenceType<RuntimeSList>{});
        visitor("std", SequenceType<Std>{});
    }
};

using SArraySequence = RuntimeArray<SArray<void*>>;
using InlineArraySequence = RuntimeArray<InlineSArray<void*, 8>>;
using StackArraySequence = RuntimeArray<StackSArray<void*>>;

template <typename Visitor>
void for_each_sequence_family(Visitor visitor)
{
    visitor("vector", VectorFamily{},
        "dn_vector_ptr; STL vector; Abseil InlinedVector<void*, 8>. Borrowed pointer elements. "
        "dn_vector_ptr default initialization allocates 64 slots; reserved initialization requests the measured size. "
        "Clear retains dn_vector_ptr capacity.");
    visitor("sarray", ArrayFamily<SArraySequence, 8>{},
        "CoreCLR SArray<void*>; STL vector; Abseil InlinedVector<void*, 8>. Borrowed pointer elements.");
    visitor("inline_sarray", ArrayFamily<InlineArraySequence, 8>{},
        "CoreCLR InlineSArray<void*, 8>; STL vector; Abseil InlinedVector<void*, 8>. "
        "Eight inline pointers; growth/clear policies differ.");
    visitor("stack_sarray", ArrayFamily<StackArraySequence, STACK_ALLOC / sizeof(void*)>{},
        "CoreCLR StackSArray<void*>; 256-byte inline buffer; STL vector; "
        "Abseil InlinedVector with 256 / sizeof(void*) inline pointers.");
    visitor("arraylist", ArrayListFamily{},
        "CoreCLR ArrayList; chunked pointer storage with five inline pointers; STL vector and deque; "
        "Abseil InlinedVector<void*, 5>. Sizes 5 and 6 straddle the inline-storage boundary. "
        "Index access walks chunks. Single-threaded workloads do not compare concurrent-reader guarantees.");
    visitor("slist", SListFamily{},
        "CoreCLR SList with head-only traits; intrusive borrowed-pointer nodes; STL forward_list uses splice_after. "
        "Both reuse preallocated nodes: construction/destruction excluded from CPU timing. "
        "SList nodes use a contiguous harness-owned array; STL nodes are individually allocator-owned. "
        "All node storage is charged to memory; pool-management fields are harness bookkeeping, "
        "not collection object bytes. No Abseil alternative.");
}

template <typename Family, typename Visitor>
void for_each_sequence_size(Visitor visitor)
{
    if constexpr (std::is_same_v<Family, ArrayListFamily>)
        for (std::uint32_t size : {5, 6})
            visitor(size);
    for (std::uint32_t size : BenchmarkSizes)
        visitor(size);
}

template <typename Sequence, typename Visitor>
void for_each_sequence_scenario(Visitor visitor)
{
    if constexpr (Sequence::intrusive)
    {
        visitor("iterate");
        visitor("unlink_relink");
    }
    else
    {
        visitor("build_growing");
        if constexpr (Sequence::supports_reserve)
            visitor("build_reserved");
        visitor("index_read");
        visitor("index_write");
        visitor("iterate");
        visitor("clear_refill");
    }
}

template <typename Sequence, typename Visitor>
void for_each_sequence_lifecycle(Visitor visitor)
{
    if constexpr (Sequence::intrusive)
        visitor("preallocated", false);
    else
    {
        visitor("growing", false);
        if constexpr (Sequence::supports_reserve)
            visitor("reserved", true);
    }
}

template <typename Sequence>
void populate_sequence(Sequence& sequence, const std::vector<void*>& values)
{
    if constexpr (Sequence::intrusive)
        sequence.fill();
    else
        for (void* value : values)
            sequence.append(value);
}

template <typename Visitor>
void describe_sequences(Visitor visitor)
{
    for_each_sequence_family([&visitor](const char* name, auto, const char* description) {
        visitor(std::string("collection.") + name, description);
    });
    visitor("note.sequences",
        "Sequence sizes count pointer elements/nodes; ns/key and B/key mean ns/element and B/element. "
        "Sequential traversal uses each implementation's traversal API. Indexed batches use deterministic shuffled indices. "
        "Builds include construction and destruction; clear_refill preserves each implementation's clear policy. "
        "Only reservable families have reserved builds/lifecycles.");
    visitor("note.realloc",
        "Untimed dn_vector_ptr reallocation uses realloc; each successful nonzero replacement counts one allocation "
        "and one deallocation, even in place. Requested live bytes replace the previous block; "
        "allocator-internal transient storage is not measured.");
    visitor("scenario.index_read",
        "Read pointer elements from a populated sequence at deterministic, shuffled indices. "
        "Indices may repeat; construction and population are excluded from timing.");
    visitor("scenario.index_write",
        "Write replacement pointer values into a populated sequence at the same deterministic indices used "
        "for indexed reads. Indices may repeat; construction and population are excluded from timing.");
    visitor("scenario.unlink_relink",
        "Move all preallocated list nodes to a spare list, then move them back. Measures both transfers "
        "without allocating or freeing nodes; node construction and destruction are excluded from timing.");
}
}

#endif
