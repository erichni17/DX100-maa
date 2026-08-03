#pragma once

#ifndef TILE_SIZE
#error "GZZ chunk policy requires TILE_SIZE"
#endif

// Keep software command/feed granularity independent of physical capacity
// above the original 16K design point. The legacy opt-out exists only to
// reproduce the pre-fix coupled treatment in attribution studies.
#if !defined(GZZ_LOGICAL_CHUNK_SIZE) &&                                  \
    !defined(GZZ_LEGACY_TILE_COUPLED_CHUNKS) && TILE_SIZE > 16384
#define GZZ_LOGICAL_CHUNK_SIZE 16384
#endif

#ifdef GZZ_LOGICAL_CHUNK_SIZE
static_assert(GZZ_LOGICAL_CHUNK_SIZE > 0,
              "GZZ logical chunk size must be positive");
static_assert(GZZ_LOGICAL_CHUNK_SIZE <= TILE_SIZE,
              "GZZ logical chunks must fit in one physical tile");
#define GZZ_LOOP_CHUNK_SIZE GZZ_LOGICAL_CHUNK_SIZE
#else
#define GZZ_LOOP_CHUNK_SIZE TILE_SIZE
#endif
