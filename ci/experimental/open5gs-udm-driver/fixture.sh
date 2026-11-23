#!/bin/sh
# Static HTTP/2 transport control; NOT an implementation of UDR or NRF.
set -eu
nghttpd --no-tls -a 0.0.0.0 -d /fixture 80 >/dev/null 2>&1 &
a=$!
nghttpd --no-tls -a 0.0.0.0 -d /fixture 7777 >/dev/null 2>&1 &
b=$!
nghttpd --no-tls -a 0.0.0.0 -d /fixture 8888 >/dev/null 2>&1 &
c=$!
trap 'kill "$a" "$b" "$c" 2>/dev/null || true' EXIT INT TERM
wait
