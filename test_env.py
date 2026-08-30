#!/usr/bin/env python3
import os
from backend.config.settings import settings

print("Environment variables:")
print(f"OBSERVABILITY_TRACING_ENABLED: {os.getenv('OBSERVABILITY_TRACING_ENABLED')}")
print(f"OBSERVABILITY_JAEGER_AGENT_HOST: {os.getenv('OBSERVABILITY_JAEGER_AGENT_HOST')}")
print(f"OBSERVABILITY_JAEGER_AGENT_PORT: {os.getenv('OBSERVABILITY_JAEGER_AGENT_PORT')}")

print("\nSettings values:")
print(f"settings.observability.tracing_enabled: {settings.observability.tracing_enabled}")
print(f"settings.observability.jaeger_agent_host: {settings.observability.jaeger_agent_host}")
print(f"settings.observability.jaeger_agent_port: {settings.observability.jaeger_agent_port}")

print(f"\nSettings observability object: {settings.observability}")