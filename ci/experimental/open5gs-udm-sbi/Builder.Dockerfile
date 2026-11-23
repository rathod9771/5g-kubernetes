FROM debian@sha256:c0a2ad73611131275b0e9a2e7544cfe3725f4268d4085d8c6f61fdd55aef7917
RUN rm -f /etc/apt/apt.conf.d/docker-clean /etc/apt/sources.list.d/* && printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20240901T000000Z bullseye main\ndeb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/20240901T000000Z bullseye-security main\n' > /etc/apt/sources.list \
 && apt-get -o Acquire::Retries=0 -o Acquire::http::Timeout=30 update \
 && DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=0 -o Acquire::http::Timeout=30 -o APT::Keep-Downloaded-Packages=true install -y --no-install-recommends build-essential binutils meson ninja-build cmake pkg-config python3 python3-setuptools python3-wheel flex bison git ca-certificates libsctp-dev libgnutls28-dev libgcrypt20-dev libssl-dev libidn11-dev libmongoc-dev libbson-dev libyaml-dev libmicrohttpd-dev libcurl4-gnutls-dev libnghttp2-dev libtins-dev libtalloc-dev patchelf curl nghttp2-server \
 && dpkg-query -W -f='${Package} ${Version}\n' > /package-versions.txt \
 && find /var/lib/apt/lists -type f -exec sha256sum {} + > /apt-index-sha256.txt \
 && find /var/cache/apt/archives -name '*.deb' -exec sha256sum {} + > /package-sha256.txt \
 && test -s /package-sha256.txt
