FROM ghcr.io/foundry-rs/foundry:latest

WORKDIR /app

# Copy project files
COPY foundry.toml .
COPY POC.sol .
COPY POCDeploy.s.sol .
COPY FullExploitPOC.t.sol .
COPY ReserveRecon.t.sol .
COPY script/ script/

# Install dependencies
RUN forge install foundry-rs/forge-std --no-git --no-commit 2>/dev/null || true

# Copy and set up entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
