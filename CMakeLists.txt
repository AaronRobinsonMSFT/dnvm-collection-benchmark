cmake_minimum_required(VERSION 3.26)
project(dnvm_collection_benchmark LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
if(NOT CMAKE_CONFIGURATION_TYPES AND NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(DNVM_SANITIZERS "Enable AddressSanitizer and UndefinedBehaviorSanitizer" OFF)
if(DNVM_SANITIZERS)
    if(MSVC)
        message(FATAL_ERROR "This sanitizer configuration requires Clang or GCC")
    endif()
    add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer)
    add_link_options(-fsanitize=address,undefined)
endif()

foreach(dependency runtime abseil-cpp benchmark)
    if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/external/${dependency}/.git")
        message(FATAL_ERROR "Missing ${dependency}: run git submodule update --init")
    endif()
endforeach()

set(ABSL_BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(ABSL_PROPAGATE_CXX_STD ON CACHE BOOL "" FORCE)
set(BENCHMARK_ENABLE_TESTING OFF CACHE BOOL "" FORCE)
set(BENCHMARK_ENABLE_INSTALL OFF CACHE BOOL "" FORCE)
set(BENCHMARK_ENABLE_WERROR OFF CACHE BOOL "" FORCE)
add_subdirectory(external/abseil-cpp EXCLUDE_FROM_ALL)
add_subdirectory(external/benchmark EXCLUDE_FROM_ALL)

set(CONTAINERS "${CMAKE_CURRENT_SOURCE_DIR}/external/runtime/src/native/containers")
set(SIZEOF_VOID_P "${CMAKE_SIZEOF_VOID_P}")
configure_file("${CONTAINERS}/dn-config.h.in" "${CMAKE_CURRENT_BINARY_DIR}/dn-config.h")
add_library(runtime_map STATIC
    "${CONTAINERS}/dn-simdhash.c"
    "${CONTAINERS}/dn-simdhash-u32-ptr.c"
    "${CONTAINERS}/dn-simdhash-ptr-ptr.c"
    "${CONTAINERS}/dn-simdhash-ptrpair-ptr.c"
    "${CONTAINERS}/dn-simdhash-string-ptr.c"
    "${CONTAINERS}/dn-simdhash-utils.c"
    "${CONTAINERS}/dn-umap.c"
    "${CONTAINERS}/dn-vector.c"
    src/runtime_platform.c)
target_include_directories(runtime_map PUBLIC "${CONTAINERS}" "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_include_directories(runtime_map PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
target_include_directories(runtime_map PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/external/runtime/src/native")
if(UNIX)
    target_link_libraries(runtime_map PUBLIC m)
endif()
if(MSVC)
    target_compile_options(runtime_map PRIVATE /std:c11)
    # Keep the object that supplies the default assertion handler's linker alias.
    target_link_options(runtime_map INTERFACE "/WHOLEARCHIVE:$<TARGET_FILE:runtime_map>")
    if(CMAKE_SIZEOF_VOID_P EQUAL 4)
        target_compile_options(runtime_map PRIVATE /arch:SSE2)
    endif()
endif()

add_library(map_adapters INTERFACE)
set(CORECLR "${CMAKE_CURRENT_SOURCE_DIR}/external/runtime/src/coreclr")
foreach(source sbuffer arraylist)
    # Replace only the VM precompiled-header include; implementation bodies stay unchanged.
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CORECLR}/utilcode/${source}.cpp")
    file(READ "${CORECLR}/utilcode/${source}.cpp" standalone)
    string(REPLACE "#include \"stdafx.h\"" "#include \"coreclr_compat.h\"" standalone "${standalone}")
    file(CONFIGURE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/coreclr/${source}.cpp" CONTENT "${standalone}" @ONLY)
endforeach()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CORECLR}/inc/utilcode.h" "${CORECLR}/utilcode/collections.cpp")
# Extract the deprecated collection, not the unrelated VM utilities in these files.
file(READ "${CORECLR}/inc/utilcode.h" chained_header)
string(FIND "${chained_header}" "typedef DPTR(struct HASHENTRY)" chained_start)
string(FIND "${chained_header}" "inline COUNT_T HashCOUNT_T" chained_end)
if(chained_start LESS 0 OR chained_end LESS chained_start)
    message(FATAL_ERROR "Cannot locate pinned CHashTable declarations")
endif()
math(EXPR chained_length "${chained_end} - ${chained_start}")
string(SUBSTRING "${chained_header}" ${chained_start} ${chained_length} chained_header)
set(chained_header "// Licensed to the .NET Foundation under one or more agreements.
// The .NET Foundation licenses this file to you under the MIT license.
#pragma once
#include \"coreclr_chained_hash_compat.h\"
${chained_header}")
file(CONFIGURE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/coreclr/chained_hash.h" CONTENT "${chained_header}" @ONLY)
file(READ "${CORECLR}/utilcode/collections.cpp" chained_source)
string(FIND "${chained_source}" "void CClosedHashBase::Delete" chained_end)
if(chained_end LESS 0)
    message(FATAL_ERROR "Cannot locate pinned CHashTable implementation")
endif()
string(SUBSTRING "${chained_source}" 0 ${chained_end} chained_source)
string(REPLACE "#include \"stdafx.h\"" "#include \"chained_hash.h\"" chained_source "${chained_source}")
string(REPLACE "#include \"utilcode.h\"" "" chained_source "${chained_source}")
string(REPLACE "#include \"ex.h\"" "" chained_source "${chained_source}")
file(CONFIGURE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/coreclr/chained_hash.cpp" CONTENT "${chained_source}" @ONLY)
add_library(runtime_sequences STATIC
    "${CMAKE_CURRENT_BINARY_DIR}/coreclr/sbuffer.cpp"
    "${CMAKE_CURRENT_BINARY_DIR}/coreclr/arraylist.cpp"
    "${CMAKE_CURRENT_BINARY_DIR}/coreclr/chained_hash.cpp")
target_include_directories(runtime_sequences PUBLIC
    "${CMAKE_CURRENT_SOURCE_DIR}/src" "${CORECLR}/inc" "${CMAKE_CURRENT_BINARY_DIR}/coreclr")
target_include_directories(runtime_sequences PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/external/runtime/src/native")
if(CMAKE_SIZEOF_VOID_P EQUAL 8)
    target_compile_definitions(runtime_sequences PUBLIC HOST_64BIT)
endif()
target_link_libraries(map_adapters INTERFACE runtime_map runtime_sequences absl::flat_hash_map absl::btree absl::inlined_vector)
target_include_directories(map_adapters INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/external/runtime/src/coreclr/inc")

add_executable(map_benchmarks benchmarks/map_benchmarks.cpp)
target_link_libraries(map_benchmarks PRIVATE map_adapters benchmark::benchmark)
add_executable(map_memory benchmarks/map_memory.cpp src/coreclr_memory.cpp)
target_link_libraries(map_memory PRIVATE map_adapters)
find_package(Git REQUIRED)
foreach(dependency runtime abseil-cpp benchmark)
    execute_process(COMMAND "${GIT_EXECUTABLE}" rev-parse HEAD
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/external/${dependency}"
        OUTPUT_VARIABLE revision OUTPUT_STRIP_TRAILING_WHITESPACE
        COMMAND_ERROR_IS_FATAL ANY)
    if(dependency STREQUAL "abseil-cpp")
        set(dependency abseil)
    endif()
    foreach(target map_benchmarks map_memory)
        target_compile_definitions(${target} PRIVATE "DNVM_${dependency}_REVISION=\"${revision}\"")
    endforeach()
endforeach()
set(DNVM_TARGET_ARCH "${CMAKE_SYSTEM_PROCESSOR}")
if(CMAKE_GENERATOR_PLATFORM STREQUAL "Win32")
    set(DNVM_TARGET_ARCH x86)
elseif(CMAKE_GENERATOR_PLATFORM STREQUAL "x64")
    set(DNVM_TARGET_ARCH x64)
endif()
foreach(target map_benchmarks map_memory)
    target_compile_definitions(${target} PRIVATE
        DNVM_COMPILER="${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}"
        DNVM_BUILD_TYPE="$<CONFIG>"
        DNVM_SYSTEM="${CMAKE_SYSTEM_NAME}"
        DNVM_ARCH="${DNVM_TARGET_ARCH}"
        DNVM_SANITIZERS="$<BOOL:${DNVM_SANITIZERS}>")
endforeach()

include(CTest)
if(BUILD_TESTING)
    add_executable(map_tests tests/map_tests.cpp src/coreclr_memory.cpp)
    target_link_libraries(map_tests PRIVATE map_adapters)
    add_test(NAME map_contract COMMAND map_tests)
    set_tests_properties(map_contract PROPERTIES TIMEOUT 60)
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    add_test(NAME report_contract COMMAND "${Python3_EXECUTABLE}" -m unittest discover
        -s "${CMAKE_CURRENT_SOURCE_DIR}/tests" -p "test_*.py")
endif()
