# SovereignFlow Scalability Guidelines

Practical guidelines for preparing SovereignFlow for safe and predictable horizontal scaling.

**1. General principles**
- **Stateless by default**: the application must not store session state or persistent data locally — use PostgreSQL, Weaviate, and external services for all state.
- **Configuration via environment variables**: all environment-specific parameters (DB URL, ports, secret keys) must come from ENV.
- **Idempotency and retry**: network operations and integrations must be idempotent or implement controlled retry with backoff.

**2. Database and connections**
- **Connection pooling**: use application-side connection pooling (`psycopg` pool) or an external pgbouncer to limit concurrent connections to PostgreSQL.
- **Connection limits**: set a DB connection limit and tune replica count / worker count to stay within it.
- **Short transactions**: avoid long-running transactions that block tables.

**3. Weaviate and external services**
- **Weaviate scaling**: run Weaviate in a mode that supports replication/HA (production cluster) or scale it vertically.
- **Rate limits and timeouts**: configure sensible timeouts and limits for model/embeddings requests, and implement error handling with retry.

**4. Runtime architecture**
- **Multiple instances + load balancer**: deploy multiple application replicas behind a load balancer (nginx, ALB, Ingress) with no sticky sessions.
- **Process model vs threads**: in production, prefer multiple processes (one or a few workers each) over a single large multi-threaded process — improves CPU utilisation and restart isolation.
- **Containerisation**: prepare a container image and deployment manifests (docker-compose, Kubernetes Deployment/Service/HPA).

**5. Ingestion and pipelines**
- **Separate ingestion from query**: move heavy document ingestion to a dedicated service/worker pool (queue: RabbitMQ, Redis Streams, SQS).
- **Batching and backpressure**: apply write batching and flow-control mechanisms under high load.

**6. Cache and JWKS**
- The local `JwksCache` is acceptable — each process holds its own cache with its own TTL. With many instances, reduce refresh frequency or place a caching layer in front of the application (e.g. a reverse proxy with cache).

**7. Health checks and graceful shutdown**
- Use the existing `live` and `ready` endpoints as readiness and liveness probes in your orchestration platform.
- Implement graceful shutdown: stop accepting new requests, finish in-flight work, and cleanly close DB and Weaviate connections.

**8. Monitoring and observability**
- **Metrics**: expose Prometheus metrics for request rate, latency, errors, DB/Weaviate usage, and queue depth.
- **Logs**: structured JSON logs with `request_id` and `trace_id` fields.
- **Tracing**: add distributed tracing (OpenTelemetry) for cross-service latency visibility.

**9. Load testing and operational readiness**
- Run load tests (k6, Locust) simulating realistic traffic: queries, ingestion, and model calls.
- Test DB and Weaviate under connection limits and expected latency.

**10. Security and authorisation**
- **JWKS and key rotation**: key rotation handling is implemented — monitor token validation errors and review TTL settings.
- **Access restrictions**: apply rate limiting, CORS, and input size limits on admin endpoints.

**11. Deployment checklist**
- [ ] Build a `Dockerfile` and container image.
- [ ] Add a connection pool (`psycopg` pool or pgbouncer) and test under maximum connections.
- [ ] Prepare Kubernetes manifests (Deployment, Service, Ingress, HPA) or a production docker-compose with a load balancer.
- [ ] Extract ingestion workers and a message queue for document processing.
- [ ] Add Prometheus metrics and structured logging.
- [ ] Run load tests and tune parameters (threads, replicas, DB pool size).
