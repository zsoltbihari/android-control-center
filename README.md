# Android Control Center

Az Android Control Center egy fejlesztés alatt álló, Linuxos, PyQt6-alapú grafikus alkalmazás Android-telefonok ADB-kapcsolaton keresztüli kezeléséhez. A képernyőtükrözési és virtuális képernyős funkciókhoz az `scrcpy` külső programot használja.

A projekt bétaállapotú. A funkciók egy része fejlesztés alatt állhat, és a tényleges működés függ az Android-verziótól, a telefon engedélyeitől, az ADB-kapcsolattól, valamint a telepített rendszerprogramoktól.

## Tartalomjegyzék

- [Kezdés](#kezdés)
- [Rendszerkövetelmények és telepítés](#rendszerkövetelmények-és-telepítés)
- [A telefon előkészítése](#a-telefon-előkészítése)
- [Indítás: főprogram és önálló modulok](#indítás-főprogram-és-önálló-modulok)
- [Funkciók és modulok](#funkciók-és-modulok)
- [Beállítások és helyi adatok](#beállítások-és-helyi-adatok)
- [Hibaelhárítás](#hibaelhárítás)
- [Biztonság és adatvédelem](#biztonság-és-adatvédelem)
- [Hibajelentés és közreműködés](#hibajelentés-és-közreműködés)
- [Licenc](#licenc)

## Kezdés

A legegyszerűbb út Debian/Ubuntu/AV Linux alatt:

```bash
sudo apt update
sudo apt install git python3 python3-pyqt6 adb scrcpy
git clone https://github.com/zsoltbihari/android-control-center.git
cd android-control-center
/usr/bin/python3 ./main.py
```

A teljes első indítási leírás az [`olvasd.txt`](olvasd.txt) fájlban található.

## Rendszerkövetelmények és telepítés

### Alapkövetelmények

- Linuxos grafikus munkakörnyezet
- Python 3
- PyQt6 ugyanahhoz a Python-környezethez telepítve, amellyel a program indul
- Android Platform Tools (`adb`)
- `scrcpy` a képernyőtükrözéshez és a virtuális képernyőkhöz
- Git a repository klónozásához

Debian/Ubuntu/AV Linux rendszeren az alapcsomagok:

```bash
sudo apt update
sudo apt install git python3 python3-pyqt6 adb scrcpy
```

Egyes kiegészítő funkciókhoz további eszközök is szükségesek vagy hasznosak lehetnek:

```bash
sudo apt install fastboot xdg-utils usbutils aapt python3-send2trash
```

Ezek nem mindegyike kötelező minden funkcióhoz. A `modules/dependencies.py` grafikus függőségellenőrzővel megtekinthetők az alapvető és opcionális összetevők.

### Letöltés

```bash
git clone https://github.com/zsoltbihari/android-control-center.git
cd android-control-center
```

A könyvtárszerkezetet tartsd meg. A `main.py` a projekt gyökerében van, a különálló modulok a `modules/` könyvtárban.

## A telefon előkészítése

1. Kapcsold be a telefon Fejlesztői beállításait.
2. Engedélyezd az USB-hibakeresést.
3. USB-kábellel csatlakoztasd a telefont a Linuxos géphez.
4. Oldd fel a telefon képernyőjét.
5. Ha megjelenik az ADB-hibakeresési engedélykérés, ellenőrizd a számítógép kulcsát, majd fogadd el.

Ellenőrzés:

```bash
adb devices -l
```

A készüléknek `device` állapotban kell megjelennie. Az `unauthorized` azt jelenti, hogy a telefonon még jóvá kell hagyni a kapcsolatot. Ha a telefon nem jelenik meg, ellenőrizd az USB-kábelt, az USB-kapcsolat módját, az USB-hibakeresést és a Linux USB-jogosultságait.

Wi-Fi-s ADB-kapcsolat is használható, ha azt a telefon és az Android-verzió támogatja, és a kapcsolatot szabályosan engedélyezted. A párosítás és a hálózati elérhetőség feltételei Android-verziónként eltérhetnek.

## Indítás: főprogram és önálló modulok

### Főprogram

A projekt gyökérkönyvtárából:

```bash
/usr/bin/python3 ./main.py
```

A `main.py` adja a központi ablakot, a menürendszert, a modulok közötti navigációt, valamint a közös beállítások kezelését.

### Minden modul külön is indítható

A `modules/` könyvtár moduljai külön Python-fájlok, saját közvetlen indítási ponttal. A főprogram nélkül, a projekt gyökérkönyvtárából az alábbi parancsokkal próbálhatók ki:

```bash
/usr/bin/python3 ./modules/applications.py
/usr/bin/python3 ./modules/connections.py
/usr/bin/python3 ./modules/contacts.py
/usr/bin/python3 ./modules/dependencies.py
/usr/bin/python3 ./modules/device_info.py
/usr/bin/python3 ./modules/diagnostics.py
/usr/bin/python3 ./modules/file_manager.py
/usr/bin/python3 ./modules/google_accounts.py
/usr/bin/python3 ./modules/screen_mirroring.py
/usr/bin/python3 ./modules/storage.py
/usr/bin/python3 ./modules/virtual_screens.py
```

**Fontos különbség:** az önálló indíthatóság azt jelenti, hogy a modul saját ablakban elindítható; nem garantálja, hogy minden funkciója pontosan ugyanúgy működik, mint a főprogram által biztosított környezetben. Különösen az `applications.py` közvetlen indítása felület-előnézeti mód: egyes, a főprogram gazdakörnyezetétől függő telefonműveletekhez a modult a `main.py`-ból kell megnyitni. Az ADB- és scrcpy-alapú műveletekhez ezeknek a külső eszközöknek továbbra is elérhetőnek kell lenniük.

## Funkciók és modulok

### Kezdő szint: csatlakozás és alapadatok

#### `connections.py` – Kapcsolatok

- ADB-szerver állapotának ellenőrzése és indítása
- USB-n csatlakozó ADB-eszközök felismerése
- Telefon IP-címeinek lekérdezése USB-kapcsolat mellett, ahol ez lehetséges
- Linuxos hálózat, hotspot és útvonal-információk megjelenítése
- Wi-Fi-s ADB-kapcsolat kezdeményezése, alapértelmezetten az 5555-ös port használatával
- A kapcsolat ellenőrzése tényleges ADB-paranccsal
- ADB-szerver újraindítása vagy leállítása
- Részletes naplózás és háttérszálas parancsvégrehajtás

#### `device_info.py` – Eszközadatok

- Gyártó, márka, modell és eszköznév
- Android-verzió és API-szint
- Buildazonosító és biztonsági javítás szintje
- MIUI-/HyperOS-verzió, ha a készülék közli
- Processzor-/hardverplatform és ABI-adatok
- Bootloader- és egyes rendszerbiztonsági tulajdonságok
- Adatok másolása és exportálása

A mezők elérhetősége az Android-verziótól és a készülék által közölt tulajdonságoktól függ.

### Haladó felhasználói szint: alkalmazások, fájlok és adatok

#### `applications.py` – Alkalmazások

- ADB-eszköz kiválasztása és az eszközlista frissítése
- A telefonon található alkalmazáscsomagok listázása
- Keresés alkalmazásnév vagy csomagnév alapján
- Felhasználói alkalmazások, rendszeralkalmazások vagy az összes alkalmazás szűrése
- Engedélyezett alkalmazásokra szűkítés
- Alkalmazásokhoz kapcsolódó további műveletek és állapotok kezelése a modul felületén

Az alkalmazáslista a telefonról, ADB-n keresztül származik. A funkciók elérhetőségét az Android korlátozásai befolyásolhatják.

#### `file_manager.py` – Fájlkezelő

- Kétpaneles fájlkezelő a telefon ADB-n keresztül elérhető fájlrendszeréhez
- Elérhető tárhely és könyvtárak böngészése
- Fájlok és könyvtárak kezelése az Android által engedélyezett határok között
- Használat a főprogram munkaterületén belül vagy külön ablakban

Nem garantált a teljes Android-fájlrendszer vagy más alkalmazások privát adatainak elérése.

#### `contacts.py` – Névjegyek

- Helyi JSON-adatállomány és biztonsági másolat
- JSON-, CSV- és VCF-import/export alapok
- Keresés, forrás szerinti szűrés, másolás és duplikátumjelzés
- Importálási előnézet és névjegyek összehasonlítása
- Szerkeszthető európai országkódtábla
- Tárcsázó- és SMS-szerkesztő felület, amely a telefon megfelelő alkalmazását nyithatja meg
- ADB-eszközfelderítés és telefonállapot-figyelés háttérszálban

**Korlátok:** a modul nem teljes Android-névjegyszinkronizáló. A helyi névjegyállomány nem feltétlenül azonos a telefon vagy a Google-fiók névjegyeivel. Az SMS-küldés nem része a jelenlegi funkcióknak.

#### `storage.py` – Tárhelyelemző

- A telefon felhasználó által elérhető tárhelyének felmérése
- Könyvtárstruktúra és fájlok vizsgálata
- Fájlméret, módosítási idő és hozzáférési adatok összegyűjtése, ahol lehetséges
- Tárhelyelemzést segítő műveletek

Az Android által korlátozott könyvtárak és adatok nem feltétlenül olvashatók.

### Power user: diagnosztika és rendszerállapot

#### `diagnostics.py` – Hálózati diagnosztika

- Aktív hálózati interfészek és IP-útvonalak vizsgálata
- Wi-Fi- és kapcsolati állapotadatok
- DNS-címek
- A rendszer által elérhető hálózati forgalmi adatok
- Diagnosztikai pillanatfelvétel és forgalmi grafikonok

A megjeleníthető információk a telefon Android-verziójától és a rendszer jogosultságaitól függenek.

#### `google_accounts.py` – Google-fiókok és FRP-diagnosztika

- ADB-n keresztüli rendszeradat-lekérdezések
- Google Play-szolgáltatásokra utaló adatok és a rendszer által kiadott fiókbejegyzések áttekintése
- Diagnosztikai információk és napló

**Ez diagnosztikai modul, nem FRP-feloldó.** Nem kerüli meg a Google-fiók védelmét, a képernyőzárat vagy az Android biztonsági engedélyeit. A lekérdezhető adatok készülékenként eltérhetnek.

#### `dependencies.py` – Függőségellenőrző

- Python, PyQt6 és alapvető rendszerprogramok jelenlétének ellenőrzése
- ADB, scrcpy és egyéb opcionális eszközök állapotának megjelenítése
- Hiányzó csomagok telepítésének kezdeményezése Debian/Ubuntu-alapú rendszereken
- Csomagfrissítés indítása
- Alapértelmezett terminál beállítása

A csomagtelepítéshez rendszergazdai jogosultság és működő csomagkezelő szükséges. A telepítési parancsokat a terminálban ellenőrizd, mielőtt jóváhagyod őket.

### Advanced: képernyőtükrözés és virtuális képernyők

#### `screen_mirroring.py` – Képernyőtükrözés

- ADB-eszközlista frissítése és készülékválasztás
- `scrcpy` indítása és leállítása
- Balra/jobbra 90 fokos forgatás és visszaállítás megkísérlése
- Ablakkezelővel kapcsolatos információk és napló

A tükrözés külön `scrcpy`-folyamatot indít. A `scrcpy` képe a jelenlegi megvalósításban nem a PyQt-főablakba van beágyazva. Az ablak forgatásának eredménye a grafikus környezettől és az ablakkezelőtől függ.

#### `virtual_screens.py` – Virtuális képernyők és alkalmazásprofilok

- `scrcpy`-alapú virtuális képernyő-/alkalmazásindítási felület
- Telefonon található alkalmazások listázása és kiválasztása
- Alkalmazásprofilok létrehozása, mentése, kiválasztása és törlése
- Profilonkénti képernyő- és indítási paraméterek beállítása
- Kedvencek kezelése
- Alkalmazás indítása a kiválasztott beállításokkal
- Futó `scrcpy`-folyamatok állapotának ellenőrzése és leállítása
- Napló megtekintése, másolása és törlése

A lehetőségek a `scrcpy` verziójától, az Android-készüléktől és az elérhető ADB-kapcsolattól függenek. A „virtuális képernyő” itt nem egy teljes, külön Android-rendszert jelent.

## Beállítások és helyi adatok

A fő beállításfájl helye:

```text
~/.config/redmi-control-center/settings.json
```

A `~` a saját felhasználói könyvtárat jelenti. Egyes modulok külön konfigurációs, helyi adat-, napló-, export- vagy biztonságimásolat-fájlokat is létrehozhatnak. A helyi névjegyadatokat, exportokat és mentéseket ne töröld addig, amíg nem ellenőrizted, hogy nincs-e bennük fontos adat.

## Hibaelhárítás

### A program nem indul el

```bash
/usr/bin/python3 --version
/usr/bin/python3 -c "import PyQt6; print('PyQt6 rendben')"
```

A projekt gyökérkönyvtárából indítsd a programot, és őrizd meg a terminál teljes hibaüzenetét.

### A telefon nem jelenik meg vagy `unauthorized`

```bash
adb devices -l
```

Oldd fel a telefont, és fogadd el az ADB-kulcs engedélyezését. Ha az eszköz nem jelenik meg, ellenőrizd a kábelt, a telefon USB-módját, az USB-hibakeresést és a Linux USB-jogosultságait.

### A képernyőtükrözés nem indul

```bash
adb version
scrcpy --version
adb devices -l
```

Előbb az ADB-kapcsolatot ellenőrizd. Ezután vizsgáld meg a `screen_mirroring.py` vagy `virtual_screens.py` naplóját, valamint az ADB és az scrcpy program elérési útját.

### Egy modul nem működik

- Indítsd el külön a modult a projekt gyökérkönyvtárából.
- Mentsd el a terminálban látható teljes hibát.
- Ellenőrizd az adott modulhoz szükséges külső eszközöket.
- Vedd figyelembe az Android-verzió és a gyártói korlátozások hatását.

## Biztonság és adatvédelem

- Csak olyan telefont csatlakoztass, amelynek kezelésére jogosult vagy.
- Az Android hibakeresési engedélykéréseit a telefonon kell jóváhagyni.
- A program nem helyettesíti a Google-fiókot, a telefon jelszavát vagy a gyártó biztonsági ellenőrzéseit.
- A helyi névjegyek, naplók, exportok és biztonsági másolatok személyes adatokat tartalmazhatnak.
- Wi-Fi-s ADB-t csak megbízható hálózaton használj, és kapcsold ki, ha már nincs rá szükség.

## Hibajelentés és közreműködés

Hibajelentéskor hasznos megadni:

- Linux-disztribúció és verzió
- Python-verzió
- PyQt6, ADB és – ha érintett – scrcpy verziója
- telefonmodell és Android-verzió
- a hiba előidézésének pontos lépései
- a teljes hiba- vagy naplóüzenet

Ne tegyél nyilvános hibajegybe jelszót, Google-fiókhoz tartozó titkos adatot, telefonszámot vagy más személyes adatot.

## Licenc

A felhasználási feltételeket a repository `LICENSE` fájlja határozza meg, ha van ilyen. Ha nincs `LICENSE` fájl, a forráskód felhasználási feltételei nincsenek egyértelműen megadva.
