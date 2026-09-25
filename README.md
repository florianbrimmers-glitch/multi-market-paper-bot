# multi-market-paper-bot

Ein **Paper-Trading-Bot** für zehn Märkte. Jeder Markt hat eine eigene, regelbasierte Strategie.
Dazu kommen eine gemeinsame Risiko-Engine, ein Backtester und tägliche Morgen- und
Abend-Briefings, die Claude schreibt. Der Bot läuft auf einem kostenlosen
[Alpaca-Paper](https://alpaca.markets/)-Konto. Das Geld ist also komplett simuliert.

> [!WARNING]
> **Nur Paper-Trading: simuliertes Geld, kein finanzielles Risiko.** Das hier ist ein Werkzeug
> zum Lernen und Ausprobieren. Es ist **keine** Anlageberatung und **kein** Weg, Geld zu
> verdienen. Die meisten automatischen Trading-Bots für Privatanleger verlieren Geld. Kein Teil
> des Codes erreicht ein echtes Brokerkonto: Die Alpaca-Adresse ist fest auf den Paper-Endpunkt
> gesetzt, alles andere wird abgelehnt. Simulierte oder im Backtest erzielte Ergebnisse sagen
> nichts über zukünftige Renditen.

## Märkte und Strategien

| Markt | Symbol | Strategie | Zeitrahmen |
|-------|--------|-----------|------------|
| S&P 500 | `SPY` | Mean Reversion: Kauf bei starkem Rückgang (z-Score überverkauft), Verkauf, sobald der Kurs zum Durchschnitt zurückkehrt | 15 Min |
| NASDAQ | `QQQ` | Mean Reversion | 15 Min |
| Bitcoin | `BTC/USD` | Momentum-Ausbruch: Kauf beim Bruch des 20-Kerzen-Hochs mit bestätigendem Volumen | 1 Std |
| Gold | `GLD` | Trendfolge: schneller über langsamem gleitendem Durchschnitt | 4 Std |
| Öl | `USO` | Trendfolge | 4 Std |
| Deutschland | `EWG` (iShares MSCI Germany ETF) | Trendfolge | 4 Std |
| DAX | `DAX` (Global X DAX Germany ETF) | Trendfolge | 4 Std |
| SAP | `SAP` (NYSE) | Mean Reversion | 15 Min |
| Deutsche Bank | `DB` (NYSE) | Mean Reversion | 15 Min |
| BioNTech | `BNTX` (Nasdaq) | Momentum-Ausbruch | 1 Std |

**Deutsche Werte:** Alpaca hat keinen Zugang zu Xetra oder Frankfurt. Die deutschen Werte sind
deshalb ihre **US-Notierungen**. Sie werden in USD und zu US-Börsenzeiten gehandelt, bilden
aber dieselben Unternehmen und Indizes ab.

**Risikoregeln für jeden Trade:**
- **Schutz-Stop:** Er liegt höchstens 1 % unter dem Einstiegskurs (`MAX_STOP_PCT`) und bleibt
  über Tage aktiv (`gtc`). Einstiegskurs und Stop werden aus dem **aktuellen Live-Kurs**
  berechnet, nicht aus dem Schlusskurs der letzten Kerze.
  - Bei ETFs und Aktien hängt der Stop als verknüpfte Order (OTO) am Kauf. Er wird erst aktiv,
    wenn der Kauf ausgeführt ist.
  - Bitcoin kann bei Alpaca keine OTO-Order nutzen. Der Stop wird deshalb direkt nach der
    Ausführung als Stop-Limit-Order gesetzt.
  - Bei jedem Check gibt eine Sicherheitsprüfung jeder offenen Position ohne Stop sofort einen.
- **Positionsgröße nach Volatilität:** Jeder Trade riskiert 0,5 % des Kontos
  (`RISK_PER_TRADE_PCT`). Der Stop-Abstand ergibt sich aus der ATR. Eine Position ist zusätzlich
  auf 25 % des Kontos (`MAX_POSITION_PCT`) und die verfügbare Kaufkraft begrenzt.
- **Korrelationsfilter:** SPY und QQQ werden nie gleichzeitig gekauft, EWG und DAX ebenfalls nicht.
- **Sperre nach einem Stop-out:** Wurde eine Position ausgestoppt, kauft der Bot diesen Markt
  6 Kerzen lang nicht neu. Das sind 24 Stunden bei 4-Stunden-Kerzen, 6 Stunden bei
  1-Stunden-Kerzen und 90 Minuten bei 15-Minuten-Kerzen.
- **Sicherer Verkauf:** Vor einem Verkauf storniert der Bot den Schutz-Stop und wartet, bis
  Alpaca die Stornierung bestätigt hat. Scheitert der Verkauf trotzdem, kommt eine Warnung, der
  Stop wird neu gesetzt und der nächste Check versucht es erneut.

**Grenzen:**
- ETFs und Aktien werden nur zu US-Börsenzeiten (15:30–22:00 Uhr deutscher Zeit) gehandelt.
  Außerhalb dieser Zeiten vermerkt der Bot `skipped:market closed`. Nur Bitcoin läuft rund um
  die Uhr.
- Solange eine Kauforder noch offen ist, schickt der Bot keine zweite (`skipped:order pending`).
- GLD und USO sind ETFs, nicht die Rohstoffe selbst.
- Die Strategien sind einfache Beispiele und weder abgestimmt noch optimiert.

## Aufbau

```
config.py        Einstellungen, Paper-Sperre, Liste der Märkte, Strategie-Parameter
models.py        pydantic-Modelle (Bar, Signal, Order, Position, Account, TradePlan, …)
indicators.py    SMA, z-Score, Donchian-Kanal, ATR (nur Standardbibliothek)
i18n.py          deutsche Zahlenformate für alle Ausgaben
strategies/      Basisklasse und die drei Strategien
risk/            Positionsgröße, Stop-Obergrenze, Korrelationsfilter
data/            Marktdaten von Alpaca (Kerzen und Live-Kurs)
broker/          Broker-Schnittstelle, Alpaca-Paper-Adapter, MockBroker für Tests
engine/          trader.py (ein Live-Check) und backtest.py (spielt Historie ab)
brief/           Morgen- und Abend-Briefings von Claude (mit Textvorlage als Ersatz),
                 Zeitsteuerung und Versand an die GitHub-Issues
run_loop.py      Dauerschleife (Checks und Briefings), wird vom Workflow genutzt
run_trade.py / run_backtest.py / run_brief.py   einzelner Check / Backtest / ein Briefing
```

Live-Betrieb und Backtester nutzen denselben Strategie- und Risiko-Code.

## Einrichtung

1. Ein kostenloses Alpaca-**Paper**-Konto anlegen und dort Paper-API-Keys erzeugen.
2. `cp .env.example .env` ausführen und die Keys eintragen. Anschließend in die Shell laden,
   zum Beispiel mit `set -a; . ./.env; set +a`.
3. `pip install -r requirements-dev.txt` ausführen.

## Benutzung

```bash
python -m pytest tests/ -v          # offline, keine Keys nötig
DRY_RUN=true  python run_trade.py   # ein Check, nur protokollieren (Standard)
DRY_RUN=false python run_trade.py   # ein Check mit PAPER-Orders
python run_backtest.py [SPY QQQ ...]
python run_brief.py morning|night
```

Jeder Check schreibt pro Markt eine JSONL-Zeile nach `trade_decisions.jsonl`, auch im `DRY_RUN`.

## Automatischer Betrieb

Im Repo unter Settings diese **Secrets** anlegen: `ALPACA_API_KEY`, `ALPACA_API_SECRET` und
`ANTHROPIC_API_KEY`. Orders werden erst platziert, wenn die Repo-**Variable** `DRY_RUN` auf
`false` steht.

**`trade-loop.yml` lässt den Bot rund um die Uhr laufen:**
- Ein Job macht etwa 5 Std. 40 Min. lang alle 15 Minuten einen Check (`run_loop.py`). Die
  Checks laufen jeweils 20 Sekunden nach der Viertelstunde, damit die letzte 15-Minuten-Kerze
  abgeschlossen ist.
- Am Ende startet der Job den nächsten selbst. Ein GitHub-Timer prüft alle 3 Stunden als
  Wachhund und startet die Kette neu, falls sie einmal abreißt. Die Concurrency-Gruppe sorgt
  dafür, dass immer nur ein Job läuft.
- Das setzt ein **öffentliches Repo** voraus, weil öffentliche Repos unbegrenzte
  Actions-Minuten haben. Ein privates Repo bräuchte etwa 43.000 Minuten im Monat.
- **Bot stoppen:** Actions → Paper Trading Loop → „…“ → *Disable workflow*.

**Tägliche Briefings** kommen aus derselben Schleife und richten sich nach der
Alpaca-Börsenuhr. Die US-Sommerzeit wird dadurch automatisch berücksichtigt.
- Das Morgen-Briefing kommt etwa 45 Minuten vor der US-Eröffnung, das Abend-Briefing direkt
  nach Börsenschluss.
- Beide kommen nur an Handelstagen.
- Jedes Briefing erscheint als Kommentar im Issue **„Tägliche Briefings“**. Weil der Bot dich
  darin erwähnt (@mention), benachrichtigt dich GitHub per Web, E-Mail oder App.
- In einem öffentlichen Repo kann jeder die Briefings und Logs lesen (Papiergeld). Die Keys
  bleiben geheim.

**Trade-Meldungen:** Jede echte Papier-Order (mit Stückzahl und Stop), jeder Verkauf, jeder
ausgelöste Stop, jede Ablehnung und jeder Fehler landet als Kommentar im Issue **„Trade-Log“**.
Du bekommst zu jeder Meldung eine Benachrichtigung und kannst die Trades verfolgen, während der
Job noch läuft. GitHub zeigt die Actions-Logs eines Jobs nämlich erst an, wenn er fertig ist.

`brief.yml` erzeugt jederzeit ein Briefing von Hand.

### Briefings als Claude-Aufgabe erhalten
Alternativ lässt sich eine geplante Claude-Aufgabe (Routine) mit demselben Zeitplan einrichten.
Sie führt `python run_brief.py morning` oder `night` in diesem Repo aus und schickt das Ergebnis
in eine Claude-Session. Man kann `run_brief.py` auch so erweitern, dass es den Text per E-Mail
oder Slack verschickt.
