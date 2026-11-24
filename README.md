# DPQS – Dinamik Öncelikli Akıllı CPU Kuyruk Yönetimi

FCFS, Round Robin, MLFQ ve DPQS algoritmalarını dört farklı senaryoda karşılaştıran CPU zamanlama simülasyonu.

## Gereksinimler
- Python 3
- matplotlib
- numpy

## Mac'te çalıştırma

Proje klasöründe terminal açın:

    python3 -m venv .venv
    source .venv/bin/activate
    python3 -m pip install -r requirements.txt
    python3 main.py --scenario all

## Windows'ta çalıştırma

Proje klasöründe PowerShell açın:

    python -m venv .venv
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
    .\.venv\Scripts\Activate.ps1
    python -m pip install -r requirements.txt
    python main.py --scenario all

## Senaryolar

- cpu: CPU-bound yoğun süreçler
- io: I/O-bound yoğun süreçler
- mixed: Kısa ve uzun süreçler
- priority: Farklı öncelik seviyeleri
- all: Tüm senaryolar

## Karşılaştırılan algoritmalar

- FCFS (First Come First Served)
- Round Robin (RR)
- MLFQ (Multilevel Feedback Queue)
- DPQS (Dynamic Priority Queue Scheduling)

## Çıktılar

Çalıştırma sonuçları results/ klasörüne kaydedilir.

- Algoritma karşılaştırma CSV dosyası
- Süreç detayları CSV dosyası
- Terminal çıktısı
- 10 farklı analiz grafiği

## Projeyi çalıştırma

Windows:

    python main.py --scenario all

Mac:

    python3 main.py --scenario all
