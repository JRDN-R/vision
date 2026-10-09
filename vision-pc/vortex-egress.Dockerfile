FROM python:3.13-slim
WORKDIR /app
# Docker Desktop runs as the signed-in user and cannot bind-mount Vision's
# administrator-only installation directory. Send this public code at build
# time instead; no Windows folder or server configuration is mounted at runtime.
COPY --chmod=0444 vortex_egress.py vortex_network.py vortex_urls.py /app/
USER 65534:65534
CMD ["python", "-B", "vortex_egress.py"]
