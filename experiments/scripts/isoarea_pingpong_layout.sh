#!/usr/bin/env bash
isoarea_validate_layout() {
  local log=$1 mode=$2 page=$3
  grep -Eq "VIRTUAL_TILE_CONSUMER_LAYOUT mode=${mode} page_elements=${page} logical_elements=16384 mem_size=2147483648" "$log"
}
