#ifndef DNVM_CORECLR_CHAINED_HASH_H
#define DNVM_CORECLR_CHAINED_HASH_H

#include "chained_hash.h"

namespace dnvm
{
class CoreChainedHash : private CHashTableAndData<CNewDataNoThrow>
{
    struct Entry : FREEHASHENTRY
    {
        std::uint32_t key;
        void* value;
    };

public:
    using key_type = std::uint32_t;
    static constexpr bool supports_replace = true;
    static constexpr bool supports_remove = true;
    static constexpr bool allows_duplicates = true;
    static constexpr bool is_set = false;

    explicit CoreChainedHash(std::uint32_t capacity = 0)
        : CHashTableAndData<CNewDataNoThrow>(101)
    {
        const std::uint32_t entries = capacity == 0 ? 101 : capacity;
        if (entries > std::numeric_limits<int>::max() / sizeof(Entry))
            throw std::bad_alloc();
        if (FAILED(NewInit(entries, sizeof(Entry), 0xffff)))
            throw std::bad_alloc();
    }
    CoreChainedHash(const CoreChainedHash&) = delete;
    CoreChainedHash& operator=(const CoreChainedHash&) = delete;

    bool insert(key_type key, void* value)
    {
        Entry* entry = reinterpret_cast<Entry*>(Add(key));
        if (entry == nullptr)
            throw std::bad_alloc();
        entry->key = key;
        entry->value = value;
        ++count_;
        return true;
    }
    bool find(key_type key, void*& value) const
    {
        const Entry* entry = lookup(key);
        if (entry == nullptr)
            return false;
        value = entry->value;
        return true;
    }
    bool replace(key_type key, void* value)
    {
        Entry* entry = lookup(key);
        if (entry == nullptr)
            return false;
        entry->value = value;
        return true;
    }
    bool erase(key_type key)
    {
        bool removed = false;
        while (Entry* entry = lookup(key))
        {
            Delete(key, static_cast<HASHENTRY*>(entry));
            --count_;
            removed = true;
        }
        return removed;
    }
    std::size_t size() const { return count_; }
    void clear()
    {
        CHashTableAndData<CNewDataNoThrow>::Clear();
        count_ = 0;
    }
    template <typename Visitor>
    void visit(Visitor& visitor) const
    {
        HASHFIND cursor;
        CHashTableAndData<CNewDataNoThrow>& table = const_cast<CoreChainedHash&>(*this);
        for (Entry* entry = reinterpret_cast<Entry*>(table.FindFirstEntry(&cursor));
             entry != nullptr; entry = reinterpret_cast<Entry*>(table.FindNextEntry(&cursor)))
            visitor(entry->key, entry->value);
    }
    std::uint32_t bucket_count() const { return m_iBuckets; }
    std::uint32_t entry_capacity() const { return m_iEntries; }

private:
    BOOL Cmp(SIZE_T key, const HASHENTRY* entry) override
    {
        return key != reinterpret_cast<const Entry*>(entry)->key;
    }
    Entry* lookup(key_type key) const
    {
        return reinterpret_cast<Entry*>(const_cast<CoreChainedHash*>(this)->Find(key, key));
    }
    std::size_t count_ = 0;
};
}

#endif
