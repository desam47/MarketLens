#!/usr/bin/env python3
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

print("=== Environment Variables ===")
print(f"OBSERVABILITY_TRACING_ENABLED: {os.getenv('OBSERVABILITY_TRACING_ENABLED')}")
print(f"OBSERVABILITY_JAEGER_AGENT_HOST: {os.getenv('OBSERVABILITY_JAEGER_AGENT_HOST')}")
print(f"OBSERVABILITY_JAEGER_AGENT_PORT: {os.getenv('OBSERVABILITY_JAEGER_AGENT_PORT')}")

print("\n=== Current Working Directory ===")
print(os.getcwd())

print("\n=== Checking .env file directly ===")
if os.path.exists('.env'):
    with open('.env', 'r') as f:
        content = f.read()
        print(".env file content:")
        print(content)
else:
    print(".env file not found")

print("\n=== Testing Pydantic Settings Directly ===")

class TestObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OBSERVABILITY_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    tracing_enabled: bool = Field(default=False)
    jaeger_agent_host: str = Field(default="localhost")
    jaeger_agent_port: int = Field(default=6831)

test_settings = TestObservabilitySettings()
print(f"Test tracing_enabled: {test_settings.tracing_enabled}")
print(f"Test jaeger_agent_host: {test_settings.jaeger_agent_host}")
print(f"Test jaeger_agent_port: {test_settings.jaeger_agent_port}")

print("\n=== Testing Full Settings ===")
# Let's check what the actual Settings class looks like
from backend.config.settings import Settings, ObservabilitySettings

print("Settings.model_config:")
print(Settings.model_config)

print("\nObservabilitySettings.model_config:")
print(ObservabilitySettings.model_config)

# Let's try to create ObservabilitySettings directly
obs_settings = ObservabilitySettings()
print(f"\nDirect ObservabilitySettings:")
print(f"  tracing_enabled: {obs_settings.tracing_enabled}")
print(f"  jaeger_agent_host: {obs_settings.jaeger_agent_host}")
print(f"  jaeger_agent_port: {obs_settings.jaeger_agent_port}")

# Let's check the source of the values
print(f"\nChecking if values come from environment or defaults:")
print(f"  tracing_enabled default: {ObservabilitySettings.model_fields['tracing_enabled'].default}")
print(f"  jaeger_agent_host default: {ObservabilitySettings.model_fields['jaeger_agent_host'].default}")
print(f"  jaeger_agent_port default: {ObservabilitySettings.model_fields['jaeger_agent_port'].default}")

# Let's see if there are any environment variables being read
from pydantic_settings import PydanticBaseSettingsSource
import inspect

# Check what sources are being used
print(f"\nSettings sources:")
# This is tricky to inspect, but let's try to manually load