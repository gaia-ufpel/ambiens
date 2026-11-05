FROM docker:27-cli

RUN apk add --no-cache bash curl jq perl

COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
