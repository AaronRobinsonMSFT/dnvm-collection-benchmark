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
add_library(runtime_map STATIC
    "${CONTAINERS}/dn-simdhash.c"
    "${CONTAINERS}/dn-simdhash-u32-ptr.c"
    "${CONTAINERS}/dn-simdhash-utils.c"
    src/runtime_platform.c)
target_include_directories(runtime_map PUBLIC "${CONTAINERS}" "${CMAKE_CURRENT_SOURCE_DIR}/src")
if(MSVC)
    target_compile_options(runtime_map PRIVATE /std:c11)
    # Keep the object that supplies the default assertion handler's linker alias.
    target_link_options(runtime_map INTERFACE "/WHOLEARCHIVE:$<TARGET_FILE:runtime_map>")
    if(CMAKE_SIZEOF_VOID_P EQUAL 4)
        target_compile_options(runtime_map PRIVATE /arch:SSE2)
    endif()
endif()

add_library(map_adapters INTERFACE)
target_link_libraries(map_adapters INTERFACE runtime_map absl::flat_hash_map)

add_executable(map_benchmarks benchmarks/map_benchmarks.cpp)
target_link_libraries(map_benchmarks PRIVATE map_adapters benchmark::benchmark)
add_executable(map_memory benchmarks/map_memory.cpp)
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
    add_executable(map_tests tests/map_tests.cpp)
    target_link_libraries(map_tests PRIVATE map_adapters)
    add_test(NAME map_contract COMMAND map_tests)
    add_test(NAME memory_accounting COMMAND map_memory)
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    add_test(NAME report_contract COMMAND "${Python3_EXECUTABLE}" -m unittest discover
        -s "${CMAKE_CURRENT_SOURCE_DIR}/tests" -p "test_*.py")
endif()
