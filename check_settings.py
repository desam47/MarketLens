#!/usr/bin/env python3
import os
from backend.config.settings import Settings

print("Current working directory:", os.getcwd())
print("Looking for .env at:", os.path.abspath(".env"))
print("Does .env exist?", os.path.exists(".env"))

# Let's also check the model config of the Settings class
print("\nSettings model config:")
print(Settings.model_config)

# Now let's try to load the settings and see the values
settings = Settings()
print("\nSettings observability:")
print(f"  tracing_enabled: {settings.observability.tracing_enabled}")
print(f"  jaeger_agent_host: {settings.observability.jaeger_agent_host}")
print(f"  jaeger_agent_port: {settings.observability.jaeger_agent_port}")

# Let's also check the raw env vars from the .env file by reading it manually
print("\nManual reading of .env file:")
if os.path.exists(".env"):
    with open(".env", 'r') as f:
        for line in f:
            print(line.strip())
else:
    print("File not found")