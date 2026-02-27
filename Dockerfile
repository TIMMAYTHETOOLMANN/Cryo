FROM ghcr.io/foundry-rs/foundry:latest

WORKDIR /app

# Copy project files and dependencies
COPY foundry.toml .
COPY .gitmodules .
COPY lib/ lib/
COPY POC.sol .
COPY POCDeploy.s.sol .
COPY FullExploitPOC.t.sol .
COPY ReserveRecon.t.sol .
COPY script/ script/

# Copy and set up entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
