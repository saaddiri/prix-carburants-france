"""Compare deux fichiers 'annee' : que s'est-il passé entre les deux ? (exploration, usage unique)

Clé d'un événement = (station, carburant, date maj). On compte :
  identiques / nouveaux / disparus (dans l'ancien, plus dans le nouveau) / modifiés (même clé, autre prix).

Usage (depuis la racine du projet, PYTHONPATH=src) :
  python explore\\diff_zips.py ANCIEN.zip NOUVEAU.zip
"""
import sys
from collections import Counter

from fuel.parse import RawPrice, parse_zip


def events(path):
    for item in parse_zip(path):
        if isinstance(item, RawPrice) and item.fuel is not None:  # ignore les noeuds <prix/> vides
            yield item


def key(p):
    return hash((p.station_id, p.fuel, p.updated_at))


def main(old_path, new_path):
    old = {key(p): hash(p.value) for p in events(old_path)}
    print(f"ancien  : {len(old):>9} événements distincts")

    seen, stations_new = set(), set()
    same = changed = 0
    new_days = Counter()
    changed_samples = {}
    for p in events(new_path):
        k = key(p)
        seen.add(k)
        stations_new.add(p.station_id)
        if k not in old:
            new_days[p.updated_at[:10]] += 1
        elif old[k] == hash(p.value):
            same += 1
        else:
            changed += 1
            if len(changed_samples) < 5:
                changed_samples[k] = p
    n_new = sum(new_days.values())
    print(f"nouveau : {len(seen):>9} événements distincts")
    print(f"\nidentiques : {same}   nouveaux : {n_new}   modifiés : {changed}   "
          f"disparus : {len(old) - same - changed}")

    print("\nNOUVEAUX par jour de maj (5 derniers jours) :")
    for day, n in sorted(new_days.items())[-5:]:
        print(f"  {day}  {n}")
    print(f"  (dont {sum(n for d, n in new_days.items() if d < sorted(new_days)[-2:][0])} datés d'avant les 2 derniers jours)")

    gone_months, gone_stations, gone_station_absent, gone_samples = Counter(), set(), set(), []
    changed_old = {}
    for p in events(old_path):
        k = key(p)
        if k in changed_samples:
            changed_old[k] = p.value
        if k not in seen:
            gone_months[p.updated_at[:7]] += 1
            gone_stations.add(p.station_id)
            if p.station_id not in stations_new:
                gone_station_absent.add(p.station_id)
            if len(gone_samples) < 5:
                gone_samples.append((p.station_id, p.fuel, p.updated_at, p.value))
    print("\nDISPARUS par mois de maj :")
    for m, n in sorted(gone_months.items()):
        print(f"  {m}  {n}")
    print(f"stations concernées : {len(gone_stations)} (dont {len(gone_station_absent)} "
          f"absentes du nouveau fichier)")
    print("exemples disparus :", gone_samples)
    print("exemples modifiés (ancien -> nouveau) :",
          [(p.station_id, p.fuel, p.updated_at, changed_old.get(k), p.value) for k, p in changed_samples.items()])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])