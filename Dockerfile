FROM ghcr.io/foundry-rs/foundry:latest

WORKDIR /app

# Copy project files and dependencies
COPY foundry.toml .
COPY .gitmodules .
COPY lib/ lib/
COPY contracts/ contracts/
COPY CollateralizationDetector.t.sol .
COPY CrossChainDetectorTest.t.sol .

# Build contracts
RUN forge build

# Copy and set up entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
