"""Discrete-event simulation with SimPy.

Optimisation answers "what is the best plan?" under assumptions. Simulation
answers a different question — "what happens to this plan when arrivals are
random and service times vary?" — and the two belong together: a schedule that
is optimal under average demand can be fragile under realistic demand, and only
a simulation will say so.

The model here is a multi-server queue with priorities, which is enough to
stress-test a staffing or capacity decision that an optimisation run produced.
"""

from __future__ import annotations

import random
import statistics

from pydantic import BaseModel, Field


class ArrivalStream(BaseModel):
    name: str
    rate_per_hour: float
    service_minutes_mean: float
    service_minutes_stddev: float = 0.0
    priority: int = 1  # lower number is served first


class SimulationRequest(BaseModel):
    servers: int = Field(ge=1)
    horizon_hours: float = Field(default=8.0, gt=0)
    streams: list[ArrivalStream]
    replications: int = Field(default=20, ge=1, le=500)
    seed: int = 0


class StreamStatistics(BaseModel):
    name: str
    arrivals: float
    served: float
    mean_wait_minutes: float
    p95_wait_minutes: float
    max_wait_minutes: float


class SimulationResult(BaseModel):
    servers: int
    horizon_hours: float
    replications: int
    server_utilisation: float
    mean_wait_minutes: float
    p95_wait_minutes: float
    mean_queue_length: float
    unserved_at_close: float
    per_stream: list[StreamStatistics] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def simulate(request: SimulationRequest) -> SimulationResult:
    """Run the queue model, averaging over independent replications.

    A single replication of a stochastic model is an anecdote; the replication
    count is what turns it into an estimate, so it is part of the request rather
    than a hidden default.
    """
    horizon_minutes = request.horizon_hours * 60.0
    per_replication = [
        _replicate(request, horizon_minutes, request.seed + n)
        for n in range(request.replications)
    ]

    all_waits = [w for rep in per_replication for waits in rep["waits"].values() for w in waits]
    result = SimulationResult(
        servers=request.servers,
        horizon_hours=request.horizon_hours,
        replications=request.replications,
        server_utilisation=statistics.fmean(r["utilisation"] for r in per_replication),
        mean_wait_minutes=statistics.fmean(all_waits) if all_waits else 0.0,
        p95_wait_minutes=_percentile(all_waits, 95),
        mean_queue_length=statistics.fmean(r["queue"] for r in per_replication),
        unserved_at_close=statistics.fmean(r["unserved"] for r in per_replication),
    )
    for stream in request.streams:
        waits = [w for rep in per_replication for w in rep["waits"][stream.name]]
        result.per_stream.append(
            StreamStatistics(
                name=stream.name,
                arrivals=statistics.fmean(r["arrivals"][stream.name] for r in per_replication),
                served=len(waits) / request.replications,
                mean_wait_minutes=statistics.fmean(waits) if waits else 0.0,
                p95_wait_minutes=_percentile(waits, 95),
                max_wait_minutes=max(waits) if waits else 0.0,
            )
        )

    offered_load = sum(
        s.rate_per_hour * s.service_minutes_mean / 60.0 for s in request.streams
    )
    if offered_load >= request.servers:
        result.notes.append(
            f"offered load is {offered_load:.2f} servers' worth against {request.servers} "
            "available, so the queue grows without bound — the waiting times below are an "
            "artefact of the finite horizon, not a steady state"
        )
    return result


def _replicate(request: SimulationRequest, horizon_minutes: float, seed: int) -> dict:
    """One independent replication. Each gets its own environment and its own
    random stream, so replications are reproducible and never interfere."""
    import simpy

    rng = random.Random(seed)
    env = simpy.Environment()
    servers = simpy.PriorityResource(env, capacity=request.servers)
    waits: dict[str, list[float]] = {s.name: [] for s in request.streams}
    arrivals: dict[str, int] = {s.name: 0 for s in request.streams}
    queue_samples: list[int] = []
    busy_minutes = 0.0

    def customer(stream: ArrivalStream):
        nonlocal busy_minutes
        arrived = env.now
        with servers.request(priority=stream.priority) as slot:
            yield slot
            waits[stream.name].append(env.now - arrived)
            duration = _service_time(rng, stream)
            # Only the part of a service that falls inside the arrival horizon
            # counts towards utilisation. The drain afterwards is not time the
            # servers were rostered for, and counting it reports above 100%.
            started = env.now
            busy_minutes += max(
                0.0, min(started + duration, horizon_minutes) - min(started, horizon_minutes)
            )
            yield env.timeout(duration)

    def source(stream: ArrivalStream):
        mean_gap = 60.0 / stream.rate_per_hour if stream.rate_per_hour > 0 else float("inf")
        while True:
            yield env.timeout(rng.expovariate(1.0 / mean_gap))
            if env.now > horizon_minutes:
                return
            arrivals[stream.name] += 1
            env.process(customer(stream))

    def sampler():
        while True:
            queue_samples.append(len(servers.queue))
            yield env.timeout(5.0)

    for stream in request.streams:
        env.process(source(stream))
    env.process(sampler())
    # Run past the horizon so work already in the system can finish. Arrivals
    # stop at the horizon, so the tail is drain time rather than extra load.
    env.run(until=horizon_minutes * 1.5)

    return {
        "waits": waits,
        "arrivals": arrivals,
        "utilisation": busy_minutes / (request.servers * horizon_minutes),
        "queue": statistics.fmean(queue_samples) if queue_samples else 0.0,
        "unserved": sum(arrivals.values()) - sum(len(w) for w in waits.values()),
    }


def _service_time(rng: random.Random, stream: ArrivalStream) -> float:
    if stream.service_minutes_stddev <= 0:
        return stream.service_minutes_mean
    return max(0.1, rng.gauss(stream.service_minutes_mean, stream.service_minutes_stddev))


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(percentile / 100.0 * (len(ordered) - 1))))
    return ordered[index]
