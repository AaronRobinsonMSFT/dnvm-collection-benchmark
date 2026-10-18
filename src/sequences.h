#ifndef DNVM_SEQUENCES_H
#define DNVM_SEQUENCES_H

#include <deque>
#include <forward_list>
#include <memory>
#include <string>
#include <vector>

#include "absl/container/inlined_vector.h"
#include "coreclr_compat.h"

namespace dnvm
{
template <typename T> struct SequenceType { using type = T; };

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

struct ArrayListFamily
{
    using RuntimeSequence = RuntimeArrayList;
    template <template <typename> class Allocator = std::allocator, typename Visitor>
    static void implementations(Visitor visitor)
    {
        using Std = StandardSequence<std::vector<void*, Allocator<void*>>, false>;
        using Deque = StandardSequence<std::deque<void*, Allocator<void*>>, false>;
        static_assert(Std::object_bytes == sizeof(std::vector<void*>), "Tracking changed vector object size");
        static_assert(Deque::object_bytes == sizeof(std::deque<void*>), "Tracking changed deque object size");
        visitor("runtime", SequenceType<RuntimeArrayList>{});
        visitor("std", SequenceType<Std>{});
        visitor("std_deque", SequenceType<Deque>{});
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
    visitor("sarray", ArrayFamily<SArraySequence, 8>{},
        "SArray<void*>; STL vector; Abseil InlinedVector<void*, 8>. Borrowed pointer elements.");
    visitor("inline_sarray", ArrayFamily<InlineArraySequence, 8>{},
        "InlineSArray<void*, 8>; STL vector; Abseil InlinedVector<void*, 8>. "
        "Eight inline pointers; growth/clear policies differ.");
    visitor("stack_sarray", ArrayFamily<StackArraySequence, STACK_ALLOC / sizeof(void*)>{},
        "StackSArray<void*>; 256-byte native inline buffer; STL vector; "
        "Abseil InlinedVector with 256 / sizeof(void*) inline pointers.");
    visitor("arraylist", ArrayListFamily{},
        "ArrayList; chunked pointer storage with five inline pointers; STL vector and deque. "
        "Index access walks chunks. Single-threaded workloads do not compare concurrent-reader guarantees.");
    visitor("slist", SListFamily{},
        "SList with head-only traits; intrusive borrowed-pointer nodes; STL forward_list uses splice_after. "
        "Both reuse preallocated nodes: construction/destruction excluded from CPU timing. "
        "Native nodes use a contiguous harness-owned array; STL nodes are individually allocator-owned. "
        "All node storage is charged to memory; pool-management fields are harness bookkeeping, "
        "not collection object bytes. No Abseil alternative.");
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
        "Sequential traversal uses native iterators. Indexed batches use deterministic shuffled indices. "
        "Builds include construction and destruction; clear_refill preserves each native clear policy. "
        "Only reservable families have reserved builds/lifecycles.");
    visitor("scenario.index_read", "One indexed read per input element; indices are dataset hashes modulo size.");
    visitor("scenario.index_write", "One indexed write per input element using the same deterministic indices.");
    visitor("scenario.unlink_relink",
        "Move every node head-first to a spare list, then move all nodes back; no allocation or deallocation. "
        "Time is per node and includes both transfers.");
}
}

#endif
