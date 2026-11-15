# Hardened Kali runtime for Bletiner AI - Phase 2/3 PoC image.
#
# docs/16 R-16: the illustrative sha256 pin in DOC-04 could not be verified, so this image pins the tag and
# records actual installed versions in /etc/bletiner/image-manifest.txt. After a verified pull, replace the
# tag with the digest reported by `docker buildx imagetools inspect kalilinux/kali-rolling`.
#
# docs/16 R-16: Go tools and Tier-4 tools (masscan, sqlmap) are deferred to the Phase 4 tooling image. This
# image carries only what Phases 2-3 need to exercise the sandbox and the egress gate.
#
# iptables is intentionally NOT installed: Kali never firewalls itself (ADR-010).
FROM kalilinux/kali-rolling

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/home/bletiner \
    USER=bletiner

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash ca-certificates coreutils curl dnsutils iproute2 iputils-ping \
        jq libcap2-bin net-tools netcat-openbsd nmap openssl procps python3 && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 bletiner && \
    useradd -u 1000 -g bletiner -m -s /bin/bash bletiner && \
    mkdir -p /workspace /etc/bletiner /home/bletiner/.config && \
    chown -R bletiner:bletiner /workspace /home/bletiner

# Strip every setuid/setgid bit: with no-new-privileges a UID-1000 process cannot use them anyway,
# and removing them closes the local-privesc vector (DOCK-002).
RUN find / -xdev -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true

# Strip FILE CAPABILITIES from all binaries (docs/16 R-09). Kali's nmap ships with setcap cap_net_raw on
# /usr/lib/nmap/nmap; under no-new-privileges the kernel refuses to exec a capability-bearing file for an
# unprivileged user (EPERM), so nmap would not even start. With the accepted zero-capability model we never
# use those caps anyway (nmap falls back to -sT), so clearing them lets the binaries exec as plain programs.
RUN getcap -r / 2>/dev/null | awk '{print $1}' | xargs -r -n1 setcap -r 2>/dev/null || true

COPY --chmod=0755 docker/kali/bletiner-exec-launcher /usr/local/bin/bletiner-exec-launcher
COPY --chmod=0755 docker/kali/container-healthcheck /usr/local/bin/container-healthcheck

# Record the real resolved versions for reproducibility evidence (R-16).
RUN { echo "# bletiner-kali image manifest (generated at build)"; \
      echo "built_at=$(date -u +%FT%TZ)"; \
      dpkg-query -W -f='${Package}=${Version}\n' nmap openssl curl dnsutils iproute2 python3 2>/dev/null; \
    } > /etc/bletiner/image-manifest.txt

WORKDIR /workspace
USER bletiner
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["/usr/local/bin/container-healthcheck"]
CMD ["tail", "-f", "/dev/null"]
