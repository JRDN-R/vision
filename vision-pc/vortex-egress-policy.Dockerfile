FROM alpine:3.23
RUN apk add --no-cache iptables
COPY vortex_egress_policy.sh /policy.sh
ENTRYPOINT ["/bin/sh", "/policy.sh"]
