# wtp-monitor

Wysyła powiadomienie push na telefon gdy pojawi się utrudnienie w komunikacji WTP.
Filtruje RSS feed WTP według słów kluczowych z pliku `config.yaml`.
Używa bezpłatnego [ntfy.sh](https://ntfy.sh) do powiadomień push.

---

## 🇵🇱 Uruchomienie

### 1. Zainstaluj apkę ntfy na telefonie

[Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) · [iOS](https://apps.apple.com/app/ntfy/id1625396347)

### 2. Sklonuj repo

```bash
git clone <url-repo>
cd <katalog-repo>
```

### 3. Utwórz środowisko wirtualne i zainstaluj zależności

```bash
python3 -m venv .venv
.venv/bin/pip install requests pyyaml
```

### 4. Skonfiguruj monitor

```bash
cp config.example.yaml config.yaml
nano config.yaml
```

Ustaw `ntfy_topic` na unikalną nazwę, np. `wtp-monitor-inicjaly-2026`.
W apce ntfy na telefonie: **+** → wpisz tę samą nazwę.

Dostosuj `keywords` do swoich potrzeb. Pusta lista (`keywords: []`) oznacza powiadomienia o **wszystkich**
utrudnieniach.

### 5. Uzupełnij plik serwisu

```bash
nano wtp-monitor.service
```

| Placeholder         | Co wpisać                                  |
|---------------------|--------------------------------------------|
| `EDIT_USER`         | Twoja nazwa użytkownika (`whoami`)         |
| `EDIT_PROJECT_PATH` | Pełna ścieżka do katalogu projektu (`pwd`) |

### 6. Zarejestruj i uruchom serwis

```bash
sudo systemctl link $(realpath wtp-monitor.service)
sudo systemctl enable --now wtp-monitor
```

### Przydatne komendy

```bash
sudo systemctl status wtp-monitor    # status
journalctl -u wtp-monitor -f         # logi na żywo
sudo systemctl restart wtp-monitor   # restart (np. po zmianie config.yaml)
sudo systemctl stop wtp-monitor      # zatrzymanie
```

---

## 🇬🇧 Setup

### 1. Install the ntfy app on your phone

[Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) · [iOS](https://apps.apple.com/app/ntfy/id1625396347)

### 2. Clone the repo

```bash
git clone <repo-url>
cd <repo-dir>
```

### 3. Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install requests pyyaml
```

### 4. Configure the monitor

```bash
cp config.example.yaml config.yaml
nano config.yaml
```

Set `ntfy_topic` to a unique name, e.g. `wtp-monitor-initials-2026`.
In the ntfy app: **+** → enter the exact same topic name.

Adjust `keywords` as needed. An empty list (`keywords: []`) sends notifications for **all** disruptions.

### 5. Fill in the service file

```bash
nano wtp-monitor.service
```

| Placeholder         | Value                                          |
|---------------------|------------------------------------------------|
| `EDIT_USER`         | Your username (`whoami`)                       |
| `EDIT_PROJECT_PATH` | Absolute path to the project directory (`pwd`) |

### 6. Register and start the service

```bash
sudo systemctl link $(realpath wtp-monitor.service)
sudo systemctl enable --now wtp-monitor
```

### Useful commands

```bash
sudo systemctl status wtp-monitor    # check status
journalctl -u wtp-monitor -f         # live logs
sudo systemctl restart wtp-monitor   # restart (e.g. after editing config.yaml)
sudo systemctl stop wtp-monitor      # stop
```