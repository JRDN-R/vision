FROM alpine:3.23
RUN apk add --no-cache socat
USER 65534:65534
# Fixed TCP relay, not a forward proxy. No request chooses the destination; both
# metadata and streaming responses go only to the isolated Cobalt API. Limit
# concurrent children and idle time without buffering a media file in memory.
ENTRYPOINT ["socat", "-T120", "TCP4-LISTEN:9000,reuseaddr,fork,max-children=16", "TCP4:network-policy:9000,connect-timeout=5"]
