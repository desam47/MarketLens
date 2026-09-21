"""
OpenTelemetry distributed tracing for MarketLens.

This module sets up OpenTelemetry tracing with OTLP exporter for
distributed tracing across services. It provides automatic instrumentation
for FastAPI, SQLAlchemy, Redis, and HTTP clients.
"""
import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from backend.config.settings import settings as _settings

logger = logging.getLogger(__name__)

# Global tracer instance
_tracer: trace.Tracer | None = None


def _get_otlp_endpoint() -> str:
    """Get OTLP endpoint from settings."""
    host = _settings.observability.jaeger_agent_host
    port = _settings.observability.jaeger_agent_port
    # Use OTLP/gRPC port (4317) for Jaeger
    return f"{host}:{port}"


def initialize_tracing() -> None:
    """Initialize OpenTelemetry tracing with OTLP exporter.

    This function should be called during application startup.
    It sets up the tracer provider, OTLP exporter, and automatic
    instrumentation for supported libraries.
    """
    global _tracer

    # Skip if tracing is disabled
    tracing_enabled = getattr(_settings.observability, 'tracing_enabled', False)
    logger.info(f"OpenTelemetry tracing enabled check: {tracing_enabled}")
    if not tracing_enabled:
        logger.info("OpenTelemetry tracing is disabled")
        return

    try:
        # Create resource with service information
        resource = Resource.create({
            "service.name": _settings.app_name,
            "service.version": _settings.app_version,
            "service.instance.id": f"{_settings.app_name}-{os.getpid()}",
        })

        # Set up tracer provider
        provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(provider)

        # Configure OTLP exporter
        otlp_exporter = OTLPSpanExporter(
            endpoint=_get_otlp_endpoint(),
            insecure=True,  # In production, use secure connection with proper certificates
        )

        # Add span processor
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

        # Get tracer
        _tracer = trace.get_tracer(__name__)

        # Instrument libraries
        _instrument_libraries()

        logger.info(
            f"OpenTelemetry tracing initialized with OTLP at "
            f"{_get_otlp_endpoint()}"
        )
        logger.info(f"Tracer initialized: {_tracer}")

    except Exception as e:
        logger.warning(f"Failed to initialize OpenTelemetry tracing: {e}")
        # Set a new TracerProvider with no span processors to indicate tracing is not working
        trace.set_tracer_provider(TracerProvider())


def _instrument_libraries() -> None:
    """Apply automatic instrumentation to supported libraries."""
    try:
        # Instrument FastAPI
        FastAPIInstrumentor().instrument()
        logger.debug("FastAPI instrumentation enabled")

        # Instrument SQLAlchemy
        SQLAlchemyInstrumentor().instrument()
        logger.debug("SQLAlchemy instrumentation enabled")

        # Instrument Redis
        RedisInstrumentor().instrument()
        logger.debug("Redis instrumentation enabled")

        # Instrument HTTPX (used by yfinance and other providers)
        HTTPXClientInstrumentor().instrument()
        logger.debug("HTTPX instrumentation enabled")

    except Exception as e:
        logger.warning(f"Failed to instrument some libraries: {e}")


def get_tracer() -> trace.Tracer | None:
    """Get the global tracer instance.

    Returns:
        The tracer instance if tracing is enabled, None otherwise.
    """
    return _tracer


def shutdown_tracing() -> None:
    """Shutdown tracing and flush remaining spans.

    This function should be called during application shutdown.
    """
    try:
        tracer_provider = trace.get_tracer_provider()
        if hasattr(tracer_provider, 'shutdown'):
            tracer_provider.shutdown()
        logger.info("OpenTelemetry tracing shut down")
    except Exception as e:
        logger.warning(f"Error shutting down tracing: {e}")
