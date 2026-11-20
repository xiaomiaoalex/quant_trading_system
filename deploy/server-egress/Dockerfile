# Build on the server from the verified official mihomo release binary.
# The build context must contain the decompressed `mihomo` file.
FROM python:3.12-slim

ENV HOME=/etc/mihomo \
    PYTHONDONTWRITEBYTECODE=1

COPY mihomo /usr/local/bin/mihomo
RUN chmod 0755 /usr/local/bin/mihomo \
    && mkdir -p /etc/mihomo \
    && chown 1000:1000 /etc/mihomo

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import socket; s=socket.create_connection(('127.0.0.1', 7890), 3); s.close()"

USER 1000:1000
WORKDIR /etc/mihomo
ENTRYPOINT ["/usr/local/bin/mihomo", "-d", "/etc/mihomo"]
