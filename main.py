"""
DPQS - Dinamik Öncelikli Akıllı CPU Kuyruk Yönetimi
FCFS, Round Robin, MLFQ ve önerilen DPQS algoritmalarını simüle eder.

Kurulum:
    python -m pip install -r requirements.txt
Çalıştırma (Windows PowerShell / Python 3.15.2):
    python3.15 main.py
    python3.15 main.py --scenario cpu
    python3.15 main.py --scenario io
    python3.15 main.py --scenario mixed
    python3.15 main.py --scenario priority
    python3.15 main.py --scenario all
    python3.15 main.py --scenario all --arrival-profile spread

Varsayılan dense profili önceki yoğun geliş aralığını (0..40 tick) korur.
spread profili gelişleri 0..240 tick aralığına yayar.
Çıktılar profile göre results/ veya results_spread/ klasörüne yazılır.
"""

from __future__ import annotations

import argparse
import csv
import random
import statistics
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Union

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# =========================
# GRAFIK TASARIM STANDARTLARI
# =========================
plt.rcParams.update({
    "figure.figsize": (11, 6),
    "figure.facecolor": "#FFFFFF",
    "axes.facecolor": "#FFFFFF",
    "axes.edgecolor": "#D1D5DB",
    "axes.linewidth": 0.8,
    "axes.grid": True,
    "grid.color": "#E5E7EB",
    "grid.linestyle": "--",
    "grid.linewidth": 0.7,
    "axes.axisbelow": True,
    "axes.titlesize": 14,
    "axes.titleweight": "bold",
    "axes.labelsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 9,
    "lines.linewidth": 2.5,
    "lines.markersize": 7,
    "savefig.dpi": 200,
})


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"

ALGORITHMS = ("FCFS", "Round Robin", "MLFQ", "DPQS")
SCENARIOS = (
    "CPU-bound Yoğun",
    "I/O-bound Yoğun",
    "Kısa ve Uzun Süreçler",
    "Farklı Öncelik Seviyeleri",
)

PROCESS_COUNT = 120
RANDOM_SEED = 42
CONTEXT_SWITCH_COST = 1
AGING_THRESHOLD = 150
# Açlık eşiği, her simülasyonun toplam süresinin %30'u olarak hesaplanır.

ReadyStore = Union[Deque["Process"], List["Process"], List[Deque["Process"]]]


@dataclass
class Process:
    pid: int
    arrival_time: int
    cpu_bursts: List[int]
    io_bursts: List[int]
    priority: int
    process_type: str
    duration_class: str
    state: str = "new"
    burst_index: int = 0
    remaining_cpu: int = 0
    waiting_time: int = 0
    ready_wait_current: int = 0
    max_ready_wait: int = 0
    response_time: Optional[int] = None
    completion_time: Optional[int] = None
    queue_level: int = 0
    enqueue_order: int = 0

    def __post_init__(self) -> None:
        self.remaining_cpu = self.cpu_bursts[0]

    @property
    def total_cpu_time(self) -> int:
        return sum(self.cpu_bursts)


@dataclass
class Result:
    scenario: str
    algorithm: str
    process_count: int
    avg_waiting: float
    avg_turnaround: float
    avg_response: float
    avg_efficiency: float
    context_switches: int
    context_switch_time: int
    cpu_utilization: float
    throughput: float
    max_waiting: int
    starvation_count: int
    starvation_threshold: int
    cpu_bound_wait: float
    io_bound_wait: float
    elapsed_ticks: int
    cpu_ticks: int
    wall_ms: float


def generate_workload(count: int, scenario: str, seed: int, arrival_profile: str = "dense") -> List[Process]:
    """Generate reproducible synthetic processes with selectable arrival density."""
    rng = random.Random(seed)

    if scenario == "CPU-bound Yoğun":
        types = ["CPU-bound"] * (count * 8 // 10)
        types += ["I/O-bound"] * (count - len(types))
    elif scenario == "I/O-bound Yoğun":
        types = ["I/O-bound"] * (count * 8 // 10)
        types += ["CPU-bound"] * (count - len(types))
    else:
        types = ["CPU-bound" if i % 2 == 0 else "I/O-bound" for i in range(count)]
    rng.shuffle(types)

    if scenario == "Farklı Öncelik Seviyeleri":
        priorities = [5] * (count * 2 // 10)
        priorities += [rng.choice((3, 4)) for _ in range(count * 3 // 10)]
        priorities += [rng.choice((1, 2)) for _ in range(count - len(priorities))]
        rng.shuffle(priorities)
    else:
        priorities = [rng.randint(1, 5) for _ in range(count)]

    processes: List[Process] = []
    for i in range(count):
        p_type = types[i]
        if scenario == "Kısa ve Uzun Süreçler":
            duration_class = "Kısa" if i % 2 == 0 else "Uzun"
        else:
            duration_class = "Normal"

        if duration_class == "Kısa":
            cpu_low, cpu_high = 1, 4
        elif duration_class == "Uzun":
            cpu_low, cpu_high = 10, 22
        elif p_type == "CPU-bound":
            cpu_low, cpu_high = 7, 18
        else:
            cpu_low, cpu_high = 2, 6

        burst_count = rng.randint(1, 3) if p_type == "CPU-bound" else rng.randint(2, 4)
        cpu_bursts = [rng.randint(cpu_low, cpu_high) for _ in range(burst_count)]
        io_bursts = [rng.randint(3, 10) for _ in range(burst_count - 1)]
        # dense: original concentrated arrivals (0..count/3); spread: wider arrival horizon.
        arrival_max = max(1, count // 3) if arrival_profile == "dense" else max(1, count * 2)
        arrival = rng.randint(0, arrival_max)

        processes.append(Process(
            pid=i + 1,
            arrival_time=arrival,
            cpu_bursts=cpu_bursts,
            io_bursts=io_bursts,
            priority=priorities[i],
            process_type=p_type,
            duration_class=duration_class,
        ))

    return sorted(processes, key=lambda p: (p.arrival_time, p.pid))


def fresh_copy(workload: List[Process]) -> List[Process]:
    return [
        Process(
            pid=p.pid,
            arrival_time=p.arrival_time,
            cpu_bursts=list(p.cpu_bursts),
            io_bursts=list(p.io_bursts),
            priority=p.priority,
            process_type=p.process_type,
            duration_class=p.duration_class,
        )
        for p in workload
    ]


def make_ready_store(algorithm: str) -> ReadyStore:
    if algorithm in ("FCFS", "Round Robin"):
        return deque()
    if algorithm == "MLFQ":
        return [deque(), deque(), deque()]
    if algorithm == "DPQS":
        return []
    raise ValueError(algorithm)


def iter_ready(store: ReadyStore, algorithm: str) -> Iterable[Process]:
    if algorithm == "MLFQ":
        for q in store:  # type: ignore[union-attr]
            yield from q
    else:
        yield from store  # type: ignore[misc]


def add_ready(store: ReadyStore, algorithm: str, p: Process, front: bool = False) -> None:
    if algorithm in ("FCFS", "Round Robin"):
        q = store
        if front:
            q.appendleft(p)  # type: ignore[union-attr]
        else:
            q.append(p)  # type: ignore[union-attr]
    elif algorithm == "MLFQ":
        queues = store
        level = min(max(p.queue_level, 0), 2)
        if front:
            queues[level].appendleft(p)  # type: ignore[index]
        else:
            queues[level].append(p)  # type: ignore[index]
    else:
        store.append(p)  # type: ignore[union-attr]


def remove_ready(store: ReadyStore, algorithm: str, p: Process) -> None:
    if algorithm == "MLFQ":
        store[p.queue_level].remove(p)  # type: ignore[index]
    else:
        store.remove(p)  # type: ignore[union-attr]


def choose_ready(store: ReadyStore, algorithm: str) -> Process:
    if algorithm in ("FCFS", "Round Robin"):
        return store[0]  # type: ignore[index]
    if algorithm == "MLFQ":
        for q in store:  # type: ignore[union-attr]
            if q:
                return q[0]
        raise IndexError("Ready queue is empty")
    # DPQS: dynamic priority plus an explicit anti-starvation guarantee.
    # A process that waits continuously for 60 ticks enters the urgent group.
    # Within that group, the process with the longest continuous wait is selected first.
    DPQS_URGENT_WAIT = 60

    def score(p: Process) -> tuple:
        aging = p.ready_wait_current / 5.0
        io_bonus = 2.0 if p.process_type == "I/O-bound" else 0.0
        priority_bonus = p.priority * 2.0
        short_job_bonus = max(0.0, 1.0 - min(p.remaining_cpu, 20) / 20.0)
        first_response_bonus = 3.0 if p.response_time is None else 0.0
        dynamic_score = (
            priority_bonus + aging + io_bonus
            + short_job_bonus + first_response_bonus
        )

        if p.ready_wait_current >= DPQS_URGENT_WAIT:
            return (1, p.ready_wait_current, dynamic_score, -p.enqueue_order)
        return (0, dynamic_score, p.ready_wait_current, -p.enqueue_order)

    return max(store, key=score)  # type: ignore[arg-type]


def quantum_for(p: Process, algorithm: str, ready_count: int) -> Optional[int]:
    if algorithm == "FCFS":
        return None
    if algorithm == "Round Robin":
        return 4
    if algorithm == "MLFQ":
        return (4, 8, 16)[min(p.queue_level, 2)]
    # DPQS: interactive processes receive a shorter slice; load slightly increases it.
    quantum = 3 if p.process_type == "I/O-bound" else 6
    if ready_count > 20:
        quantum += 2
    return min(quantum, 10)


def simulate(
    workload: List[Process],
    algorithm: str,
    scenario: str,
    context_cost: int = CONTEXT_SWITCH_COST,
) -> tuple[Result, List[dict]]:
    if context_cost < 0:
        raise ValueError("Context switch cost cannot be negative.")

    processes = fresh_copy(workload)
    ready = make_ready_store(algorithm)
    blocked: Dict[int, tuple[Process, int]] = {}
    current: Optional[Process] = None
    current_quantum: Optional[int] = None
    quantum_used = 0
    last_cpu_pid: Optional[int] = None
    now = 0
    cpu_ticks = 0
    context_switches = 0
    context_time = 0
    enqueue_counter = 0
    started = time.perf_counter()
    gantt: List[dict] = []

    def enqueue(p: Process, front: bool = False) -> None:
        nonlocal enqueue_counter
        p.state = "ready"
        p.ready_wait_current = 0
        p.enqueue_order = enqueue_counter
        enqueue_counter += 1
        add_ready(ready, algorithm, p, front)

    def release_events() -> None:
        for p in processes:
            if p.state == "new" and p.arrival_time <= now:
                enqueue(p)
        for pid, (p, wake_time) in list(blocked.items()):
            if wake_time <= now:
                del blocked[pid]
                enqueue(p)

    def promote_aged_mlfq() -> None:
        if algorithm != "MLFQ":
            return
        queues = ready  # three FIFO queues
        # Move each eligible process up one level; no process can move more than one
        # level during a single tick.
        for level in (2, 1):
            q = queues[level]
            keep = deque()
            while q:
                p = q.popleft()
                if p.ready_wait_current >= AGING_THRESHOLD:
                    p.queue_level = level - 1
                    # Aging changes queue level, but does not end the continuous wait.
                    p.enqueue_order = enqueue_counter + p.pid
                    queues[level - 1].append(p)
                else:
                    keep.append(p)
            queues[level] = keep

    def advance_tick(is_cpu: bool = False) -> None:
        nonlocal now, cpu_ticks
        for p in list(iter_ready(ready, algorithm)):
            p.waiting_time += 1
            p.ready_wait_current += 1
            p.max_ready_wait = max(p.max_ready_wait, p.ready_wait_current)
        now += 1
        if is_cpu:
            cpu_ticks += 1
        release_events()
        promote_aged_mlfq()

    while any(p.state != "terminated" for p in processes):
        release_events()

        if current is None:
            if not any(True for _ in iter_ready(ready, algorithm)):
                future = [p.arrival_time for p in processes if p.state == "new"]
                future.extend(wake for _, wake in blocked.values())
                if not future:
                    break
                next_time = min(future)
                if next_time > now:
                    # Idle time is included in elapsed time but not in CPU utilization.
                    gantt.append({"start": now, "end": next_time, "pid": "IDLE", "state": "idle"})
                    now = next_time
                    last_cpu_pid = None
                release_events()
                continue

            selected = choose_ready(ready, algorithm)
            # Context-switch overhead is charged when dispatching a different process
            # from the previously running process. First dispatch and idle->process
            # dispatches are not counted as process-to-process switches.
            if last_cpu_pid is not None and selected.pid != last_cpu_pid:
                if context_cost:
                    context_switches += 1
                    start = now
                    for _ in range(context_cost):
                        advance_tick(is_cpu=False)
                        context_time += 1
                    gantt.append({"start": start, "end": now, "pid": "CONTEXT", "state": "context"})
                else:
                    context_switches += 1

            # Arrivals and MLFQ aging may change the best candidate during
            # context-switch overhead, so choose again immediately before dispatch.
            selected = choose_ready(ready, algorithm)
            remove_ready(ready, algorithm, selected)
            selected.ready_wait_current = 0
            current = selected
            current.state = "running"
            current_quantum = quantum_for(current, algorithm, sum(1 for _ in iter_ready(ready, algorithm)) + 1)
            quantum_used = 0
            last_cpu_pid = current.pid
            if current.response_time is None:
                current.response_time = now - current.arrival_time

        start = now
        current.remaining_cpu -= 1
        quantum_used += 1
        advance_tick(is_cpu=True)
        gantt.append({"start": start, "end": now, "pid": current.pid, "state": "running"})

        if current.remaining_cpu == 0:
            if current.burst_index < len(current.io_bursts):
                io_time = current.io_bursts[current.burst_index]
                current.burst_index += 1
                current.state = "waiting"
                blocked[current.pid] = (current, now + io_time)
                current.remaining_cpu = current.cpu_bursts[current.burst_index]
            else:
                current.state = "terminated"
                current.completion_time = now
            current = None
            current_quantum = None
            quantum_used = 0
            continue

        if current_quantum is not None and quantum_used >= current_quantum:
            old = current
            if algorithm == "MLFQ":
                old.queue_level = min(old.queue_level + 1, 2)
            enqueue(old)
            current = None
            current_quantum = None
            quantum_used = 0

    finished = [p for p in processes if p.completion_time is not None]
    if len(finished) != len(processes):
        unfinished = [p.pid for p in processes if p.completion_time is None]
        raise RuntimeError(f"Simulation incomplete; unfinished PIDs: {unfinished[:10]}")

    waits = [p.waiting_time for p in finished]
    turns = [p.completion_time - p.arrival_time for p in finished]
    responses = [p.response_time or 0 for p in finished]
    total_cpu_time = sum(p.total_cpu_time for p in finished)
    total_waiting_time = sum(p.waiting_time for p in finished)
    # Aggregate efficiency: total CPU execution / total ready-queue waiting.
    efficiencies = total_cpu_time / total_waiting_time if total_waiting_time else 0.0
    cpu_waits = [p.waiting_time for p in finished if p.process_type == "CPU-bound"]
    io_waits = [p.waiting_time for p in finished if p.process_type == "I/O-bound"]
    elapsed = now
    wall_ms = (time.perf_counter() - started) * 1000.0

    # Sabit 50 tick eşiği yoğun iş yüklerinde neredeyse tüm süreçleri
    # aç kalmış gösteriyordu. Eşik artık simülasyon süresine göre ölçeklenir.
    starvation_threshold = max(100, round(elapsed * 0.30))

    result = Result(
        scenario=scenario,
        algorithm=algorithm,
        process_count=len(finished),
        avg_waiting=statistics.mean(waits) if waits else 0.0,
        avg_turnaround=statistics.mean(turns) if turns else 0.0,
        avg_response=statistics.mean(responses) if responses else 0.0,
        avg_efficiency=efficiencies,
        context_switches=context_switches,
        context_switch_time=context_time,
        cpu_utilization=(cpu_ticks / elapsed * 100.0) if elapsed else 0.0,
        throughput=(len(finished) / elapsed) if elapsed else 0.0,
        max_waiting=max(waits, default=0),
        starvation_count=sum(p.max_ready_wait >= starvation_threshold for p in finished),
        starvation_threshold=starvation_threshold,
        cpu_bound_wait=statistics.mean(cpu_waits) if cpu_waits else 0.0,
        io_bound_wait=statistics.mean(io_waits) if io_waits else 0.0,
        elapsed_ticks=elapsed,
        cpu_ticks=cpu_ticks,
        wall_ms=wall_ms,
    )

    detail_rows = []
    for p in finished:
        detail_rows.append({
            "Senaryo": scenario,
            "Algoritma": algorithm,
            "PID": p.pid,
            "Gelis Zamani": p.arrival_time,
            "Surec Turu": p.process_type,
            "Sure Sinifi": p.duration_class,
            "Oncelik": p.priority,
            "Toplam CPU Suresi": p.total_cpu_time,
            "Bekleme Suresi": p.waiting_time,
            "Maksimum Kesintisiz Hazir Bekleme": p.max_ready_wait,
            "Starvation (30% toplam sure)": p.max_ready_wait >= starvation_threshold,
            "Acilik Esigi (tick)": starvation_threshold,
            "Tamamlanma Zamani": p.completion_time,
            "Turnaround": p.completion_time - p.arrival_time,
            "Yanit Suresi": p.response_time,
            "Verimlilik (CPU/Bekleme)": (
                round(p.total_cpu_time / p.waiting_time, 4)
                if p.waiting_time > 0 else "Tanimsiz (bekleme 0)"
            ),
        })
    return result, detail_rows


def aggregate_row(r: Result) -> dict:
    return {
        "Senaryo": r.scenario,
        "Algoritma": r.algorithm,
        "Surec Sayisi": r.process_count,
        "Ortalama Bekleme (tick)": round(r.avg_waiting, 3),
        "Ortalama Tamamlanma (tick)": round(r.avg_turnaround, 3),
        "Ortalama Yanit (tick)": round(r.avg_response, 3),
        "Ortalama Verimlilik (CPU/Bekleme)": round(r.avg_efficiency, 4),
        "Baglam Degistirme Sayisi": r.context_switches,
        "Baglam Degistirme Maliyeti (tick)": r.context_switch_time,
        "CPU Kullanimi (%)": round(r.cpu_utilization, 2),
        "Verim (surec/tick)": round(r.throughput, 5),
        "En Uzun Bekleme (tick)": r.max_waiting,
        "Acilik Esigi (tick)": r.starvation_threshold,
        "Starvation Sayisi (kesintisiz hazir bekleme >= esik)": r.starvation_count,
        "CPU-bound Bekleme": round(r.cpu_bound_wait, 3),
        "I/O-bound Bekleme": round(r.io_bound_wait, 3),
        "Toplam Simulasyon (tick)": r.elapsed_ticks,
        "CPU Calisma (tick)": r.cpu_ticks,
        "Kod Calisma (ms)": round(r.wall_ms, 3),
    }


def save_csv(results: List[Result], details: List[dict]) -> None:
    rows = [aggregate_row(r) for r in results]
    with (RESULTS / "algorithm_comparison.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    with (RESULTS / "process_details.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(details[0].keys()))
        writer.writeheader()
        writer.writerows(details)


def find_result(results: List[Result], scenario: str, algorithm: str) -> Result:
    return next(r for r in results if r.scenario == scenario and r.algorithm == algorithm)


def save_plot(name: str) -> None:
    plt.tight_layout()
    plt.savefig(RESULTS / name, dpi=150, bbox_inches="tight")
    plt.close()


def create_graphs(results: List[Result], seed: int) -> None:
    active_scenarios = [s for s in SCENARIOS if any(r.scenario == s for r in results)]
    if not active_scenarios:
        raise ValueError('Grafik oluşturmak için sonuç bulunamadı.')
    # 1: Waiting time
    fig, ax = plt.subplots(figsize=(10, 5))
    for s in active_scenarios:
        ax.plot(ALGORITHMS, [find_result(results, s, a).avg_waiting for a in ALGORITHMS], marker="o", label=s)
    ax.set_title("1. Senaryolara Göre Ortalama Bekleme Süresi")
    ax.set_ylabel("Tick")
    ax.legend(fontsize=8)
    save_plot("01_ortalama_bekleme.png")

    # 2: Turnaround
    fig, ax = plt.subplots(figsize=(10, 5))
    for s in active_scenarios:
        ax.plot(ALGORITHMS, [find_result(results, s, a).avg_turnaround for a in ALGORITHMS], marker="o", label=s)
    ax.set_title("2. Ortalama Tamamlanma (Turnaround) Süresi")
    ax.set_ylabel("Tick")
    ax.legend(fontsize=8)
    save_plot("02_tamamlanma_suresi.png")

    # 3: Context switches
    fig, ax = plt.subplots(figsize=(10, 5))
    width = 0.18
    x = list(range(len(ALGORITHMS)))
    for i, s in enumerate(active_scenarios):
        offset = (i - (len(active_scenarios) - 1) / 2) * width
        vals = [find_result(results, s, a).context_switches for a in ALGORITHMS]
        ax.bar([v + offset for v in x], vals, width=width, label=s)
    ax.set_xticks(x, ALGORITHMS)
    ax.set_title("3. Bağlam Değiştirme Sayısı")
    ax.set_ylabel("Adet")
    ax.legend(fontsize=8)
    save_plot("03_baglam_degistirme.png")

    # 4: CPU utilization
    fig, ax = plt.subplots(figsize=(10, 5))
    for s in active_scenarios:
        ax.plot(ALGORITHMS, [find_result(results, s, a).cpu_utilization for a in ALGORITHMS], marker="o", label=s)
    ax.set_title("4. CPU Kullanım Oranı")
    ax.set_ylabel("%")
    ax.set_ylim(0, 105)
    ax.legend(fontsize=8)
    save_plot("04_cpu_kullanimi.png")

    # 5: CPU vs IO waiting
    fig, ax = plt.subplots(figsize=(9, 5))
    sample = "CPU-bound Yoğun" if "CPU-bound Yoğun" in active_scenarios else active_scenarios[0]
    x = list(range(len(ALGORITHMS)))
    width = 0.35
    ax.bar([i - width / 2 for i in x], [find_result(results, sample, a).cpu_bound_wait for a in ALGORITHMS], width, label="CPU-bound")
    ax.bar([i + width / 2 for i in x], [find_result(results, sample, a).io_bound_wait for a in ALGORITHMS], width, label="I/O-bound")
    ax.set_xticks(x, ALGORITHMS)
    ax.set_title("5. Süreç Türüne Göre Ortalama Bekleme")
    ax.set_ylabel("Tick")
    ax.legend()
    save_plot("05_surec_turune_gore_bekleme.png")

    # 6: Scaling
    counts = [25, 50, 100, 200]
    scale = {a: [] for a in ALGORITHMS}
    for n in counts:
        work = generate_workload(n, "CPU-bound Yoğun", seed + n, arrival_profile=ACTIVE_ARRIVAL_PROFILE)
        for a in ALGORITHMS:
            r, _ = simulate(work, a, "Ölçeklenebilirlik", CONTEXT_SWITCH_COST)
            scale[a].append(r.avg_waiting)
    fig, ax = plt.subplots(figsize=(9, 5))
    for a in ALGORITHMS:
        ax.plot(counts, scale[a], marker="o", label=a)
    ax.set_title("6. Süreç Sayısının Bekleme Süresine Etkisi")
    ax.set_xlabel("Süreç sayısı")
    ax.set_ylabel("Ortalama bekleme (tick)")
    ax.legend()
    save_plot("06_surec_sayisi_olceklenebilirlik.png")

    # 7: Maximum wait
    fig, ax = plt.subplots(figsize=(10, 5))
    width = 0.18
    x = list(range(len(ALGORITHMS)))
    for i, s in enumerate(active_scenarios):
        offset = (i - (len(active_scenarios) - 1) / 2) * width
        vals = [find_result(results, s, a).max_waiting for a in ALGORITHMS]
        ax.bar([v + offset for v in x], vals, width=width, label=s)
    ax.set_xticks(x, ALGORITHMS)
    ax.set_title("7. En Uzun Bekleme Süresi")
    ax.set_ylabel("Tick")
    ax.legend(fontsize=8)
    save_plot("07_maksimum_bekleme.png")



def print_summary(results: List[Result]) -> None:
    print("\n" + "=" * 76)
    print("DPQS - CPU ZAMANLAMA ALGORİTMALARI KARŞILAŞTIRMASI")
    print("=" * 76)
    active_scenarios = [s for s in SCENARIOS if any(r.scenario == s for r in results)]
    for s in active_scenarios:
        print(f"\nSENARYO: {s}")
        print("-" * 76)
        for a in ALGORITHMS:
            r = find_result(results, s, a)
            print(
                f"{a:12} | Bekleme: {r.avg_waiting:8.2f} | "
                f"Turnaround: {r.avg_turnaround:8.2f} | "
                f"CPU: %{r.cpu_utilization:6.2f} | "
                f"Context switch: {r.context_switches:4d} | "
                f"Verimlilik: {r.avg_efficiency:7.3f}"
            )
    print(f"\nCSV dosyaları ve grafikler {RESULTS.name}/ klasörüne kaydedildi.")


def print_process_details(details: List[dict]) -> None:
    """Her algoritmanın her sürecine ait test verilerini terminale basar."""
    print("\n" + "=" * 118)
    print("SÜREÇ BAZINDA TEST VERİLERİ")
    print("=" * 118)
    grouped = {}
    for row in details:
        key = (row["Senaryo"], row["Algoritma"])
        grouped.setdefault(key, []).append(row)

    for (scenario, algorithm), rows in grouped.items():
        print(f"\nSENARYO: {scenario} | ALGORİTMA: {algorithm} | SÜREÇ SAYISI: {len(rows)}")
        print("-" * 118)
        print(
            f"{'PID':>4} {'Tür':<10} {'Öncelik':>8} {'CPU':>7} "
            f"{'Bekleme':>10} {'Max Hazır':>10} {'Starve':>7} "
            f"{'Tamamlanma':>12} {'Turnaround':>12} {'Yanıt':>8} {'Verimlilik':>12}"
        )
        print("-" * 118)
        table_lines = []
        for row in sorted(rows, key=lambda item: int(item["PID"])):
            efficiency = row["Verimlilik (CPU/Bekleme)"]
            table_lines.append(
                f"{int(row['PID']):>4} "
                f"{row['Surec Turu']:<10} "
                f"{row['Oncelik']:>8} "
                f"{row['Toplam CPU Suresi']:>7} "
                f"{row['Bekleme Suresi']:>10} "
                f"{row['Maksimum Kesintisiz Hazir Bekleme']:>10} "
                f"{'EVET' if row['Starvation (30% toplam sure)'] else 'HAYIR':>7} "
                f"{row['Tamamlanma Zamani']:>12} "
                f"{row['Turnaround']:>12} "
                f"{row['Yanit Suresi']:>8} "
                f"{efficiency:>12}"
            )
        print("\n".join(table_lines))


class TeeOutput:
    """Terminal çıktısını aynı anda ekrana ve results/terminal_output.txt dosyasına yazar."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, message):
        # Her karakter/satırda diske flush yapmak yavaştır.
        # Akışı tamponlayıp yalnızca flush çağrıldığında diske aktar.
        for stream in self.streams:
            stream.write(message)
        return len(message)

    def flush(self):
        for stream in self.streams:
            stream.flush()


SCENARIO_ARGUMENTS = {
    "cpu": SCENARIOS[0],
    "io": SCENARIOS[1],
    "mixed": SCENARIOS[2],
    "priority": SCENARIOS[3],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DPQS CPU zamanlama algoritmaları simülasyonu"
    )
    parser.add_argument(
        "--scenario",
        choices=("cpu", "io", "mixed", "priority", "all"),
        default="all",
        help="cpu, io, mixed, priority veya all (varsayılan: all)",
    )
    parser.add_argument(
        "--arrival-profile", choices=("dense", "spread"), default="dense",
        help="dense: yoğun gelişler; spread: 0..240 tick aralığına yayılmış gelişler",
    )
    return parser.parse_args()


ACTIVE_ARRIVAL_PROFILE = "dense"


def main() -> None:
    global RESULTS, ACTIVE_ARRIVAL_PROFILE
    args = parse_args()
    ACTIVE_ARRIVAL_PROFILE = args.arrival_profile
    RESULTS = ROOT / ("results_spread" if args.arrival_profile == "spread" else "results")
    RESULTS.mkdir(parents=True, exist_ok=True)
    log_file = (RESULTS / "terminal_output.txt").open("w", encoding="utf-8")
    original_stdout = sys.stdout
    sys.stdout = TeeOutput(original_stdout, log_file)
    try:
        selected_scenarios = (
            list(SCENARIOS) if args.scenario == "all"
            else [SCENARIO_ARGUMENTS[args.scenario]]
        )

        all_results: List[Result] = []
        all_details: List[dict] = []

        print("DPQS simülasyonu başlatılıyor...")
        print("Seçilen test:", args.scenario)
        print("Süreç geliş profili:", "Yoğun (0..40 tick)" if args.arrival_profile == "dense" else "Yayılmış (0..240 tick)")
        print("Her algoritmanın özet sonuçları ve süreç bazındaki verileri aşağıda gösterilecektir.")

        for scenario in selected_scenarios:
            scenario_index = SCENARIOS.index(scenario)
            workload = generate_workload(
                PROCESS_COUNT, scenario, RANDOM_SEED + scenario_index * 100,
                arrival_profile=args.arrival_profile,
            )
            print(f"\nSenaryo çalıştırılıyor: {scenario}")
            for algorithm in ALGORITHMS:
                print(f"  -> {algorithm} simülasyonu...")
                result, details = simulate(
                    workload, algorithm, scenario, CONTEXT_SWITCH_COST
                )
                all_results.append(result)
                all_details.extend(details)

        save_csv(all_results, all_details)
        create_graphs(all_results, RANDOM_SEED)
        print_summary(all_results)
        print_process_details(all_details)
        print(f"\nBitti. CSV dosyaları, grafikler ve terminal_output.txt {RESULTS.name}/ klasörüne kaydedildi.")
        print("Sonuç klasörü:", RESULTS)
        sys.stdout.flush()
    finally:
        sys.stdout = original_stdout
        log_file.close()


if __name__ == "__main__":
    main()
