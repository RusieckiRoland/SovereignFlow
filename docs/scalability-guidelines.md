# Zasady i rekomendacje skalowania SovereignFlow

Poniższy dokument zawiera praktyczne zasady i kroki, które należy zastosować, aby przygotować aplikację SovereignFlow do bezpiecznego i przewidywalnego skalowania poziomego.

**1. Ogólne zasady**
- **Stateless by default**: aplikacja powinna nie przechowywać stanu sesji ani trwałych danych lokalnie — używać PostgreSQL, Weaviate i zewnętrznych usług do przechowywania stanu.
- **Konfiguracja przez zmienne środowiskowe**: wszystkie parametry zależne od środowiska (DB URL, porty, tajne klucze) powinny przychodzić z ENV.
- **Idempotentność i retry**: operacje sieciowe i integracje powinny być idempotentne lub mieć kontrolowane retry z backoff.

**2. Baza danych i połączenia**
- **Connection pooling**: użyć puli połączeń po stronie aplikacji (np. `psycopg` + pool) lub zewnętrznego pgbouncer, aby ograniczyć liczbę równoczesnych połączeń do PostgreSQL.
- **Limity połączeń**: ustawić limit połączeń w DB i dostosować liczbę replik aplikacji / workerów tak, by nie przekroczyć limitu.
- **Transakcje krótkie**: unikać długotrwałych transakcji blokujących tabele.

**3. Weaviate i zewnętrzne usługi**
- **Skalowanie Weaviate**: Weaviate powinien być uruchamiany w trybie, który obsługuje replikację/HA (produkcyjny cluster) lub skalowane pionowo.
- **Rate limits i timeouty**: ustawić sensowne timeouty i limity dla zapytań do modelu/embeddings, oraz mechanizmy błędów i retry.

**4. Architektura uruchomieniowa**
- **Wiele instancji + load balancer**: wdrożyć wiele replik aplikacji za load balancerem (NGINX, ALB, Ingress), bez sticky sessions.
- **Process model vs threads**: w produkcji preferować uruchomienie wielu procesów (po jednej lub kilka workerów) zamiast jednego dużego procesu wielowątkowego — ułatwia to wykorzystanie CPU i restart.
- **Konteneryzacja**: przygotować obraz kontenera i manifesty (docker-compose, Kubernetes Deployment/Service/HorizontalPodAutoscaler).

**5. Ingest i pipeline'y**
- **Oddzielić ingestion od query**: ciężkie zadania ingestii dokumentów warto przenieść do odrębnego serwisu/workerów (kolejka: RabbitMQ, Redis streams, SQS).
- **Batching i backpressure**: stosować batchowanie zapisów i mechanizmy ograniczające przepływ przy dużym natężeniu.

**6. Cache i JWKS**
- `JwksCache` lokalny jest akceptowalny — pamiętać, że każdy proces ma swój cache i TTL; przy dużej liczbie instancji zmniejszyć częstotliwość odświeżania lub umieścić warstwę cache przed aplikacją (np. reverse proxy z cache).

**7. Healthchecks, readiness i graceful shutdown**
- Implementować readiness i liveness endpoints (już są `live` i `ready`) i wykorzystywać je w platformie orkiestracji.
- Implementować graceful shutdown aby zakończyć przyjmowanie nowych requestów i dokończyć pracę w toku (zamknąć połączenia DB, Weaviate, zakończyć workers).

**8. Monitoring i obserwowalność**
- **Metrics**: wystawiać metryki (Prometheus) dla requestów, latency, błędów, użycia DB/Weaviate, kolejek.
- **Logi**: strukturyzowane logi (JSON) z identyfikacją request_id i trace_id.
- **Tracing**: dodać rozproszone śledzenie (OpenTelemetry) dla opóźnień między usługami.

**9. Testy obciążeniowe i gotowość operacyjna**
- Uruchomić testy load (k6, locust) symulujące rzeczywisty wzorzec ruchu: zapytań, ingestów, zapytań do modeli.
- Testować skalowanie DB i Weaviate (połączenia, limity, opóźnienia).

**10. Bezpieczeństwo i autoryzacja**
- **JWKS i rotacja kluczy**: obsługa rotacji kluczy jest zaimplementowana — monitorować błędy w walidacji tokenów i ustawienia TTL.
- **Ograniczenia dostępu**: rate limiting, CORS, limit wejścia dla admin endpoints.

**11. Checklista do wdrożenia**
- [ ] Przygotować `Dockerfile` i obrazy aplikacji.
- [ ] Dodać connection pool (psycopg pool lub pgbouncer) i przetestować maks. połączeń.
- [ ] Przygotować manifesty Kubernetes (Deployment, Service, Ingress, HPA) lub prawidłowy docker-compose z load balancerem.
- [ ] Wydzielić ingestion workers i kolejkę do przetwarzania dokumentów.
- [ ] Dodać Prometheus metrics i logowanie strukturyzowane.
- [ ] Przeprowadzić testy obciążeniowe i dopracować parametry (threads, replicas, DB pool size).

---

Jeżeli chcesz, mogę: 1) przygotować przykładowy `Dockerfile` i `k8s` manifesty; 2) dodać wzorzec puli połączeń w miejscu użycia `psycopg.connect`; lub 3) wygenerować skrypt `load test` z `k6`.
