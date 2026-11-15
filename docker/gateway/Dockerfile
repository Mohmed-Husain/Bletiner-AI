# Dual-homed egress gateway for Bletiner AI (DOC-04 section 3.1; ADR-009/012/016/017/018).
# Alpine + iptables + the stdlib/dnslib gateway runtime copied from bletiner.network.*.
FROM python:3.12-alpine

RUN apk add --no-cache iptables ip6tables tcpdump bind-tools curl openssl && \
    pip install --no-cache-dir "dnslib==0.9.24"

# The gateway runtime is dependency-isolated (docs/16 R-17): only these pure modules are needed.
WORKDIR /opt/bletiner
COPY bletiner/__init__.py bletiner/__init__.py
COPY bletiner/models/__init__.py bletiner/models/__init__.py
COPY bletiner/models/errors.py bletiner/models/errors.py
COPY bletiner/models/netaddr.py bletiner/models/netaddr.py
COPY bletiner/network/__init__.py bletiner/network/__init__.py
COPY bletiner/network/policy.py bletiner/network/policy.py
COPY bletiner/network/sni.py bletiner/network/sni.py
COPY bletiner/network/dns_filter.py bletiner/network/dns_filter.py
COPY bletiner/network/proxy.py bletiner/network/proxy.py
COPY bletiner/network/gateway_main.py bletiner/network/gateway_main.py
ENV PYTHONPATH=/opt/bletiner

COPY --chmod=0755 docker/gateway/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --chmod=0755 docker/gateway/healthcheck.sh /usr/local/bin/gateway-healthcheck

HEALTHCHECK --interval=15s --timeout=5s --start-period=3s --retries=3 \
    CMD ["/usr/local/bin/gateway-healthcheck"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
