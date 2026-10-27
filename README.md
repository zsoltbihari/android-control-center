REDMI CONTROL CENTER
====================

A Redmi Control Center egy Linuxos grafikus kezeloprogram Android-telefon ADB-kapcsolaton keresztuli eleresehez. A felulet PyQt6-ra epul, a telefonos muveletekhez az Android Debug Bridge (ADB), a kepernyotukrozeshez pedig az scrcpy kulso programot hasznalja.

A program modulokra osztott: a main.py adja a foablakot, a menurendszert, a munkateruletet, a temakat es a beallitasokat. A reszletes funkciokat a modules konyvtarban talalhato modulok valositjak meg.

FONTOS: a program Android-hibakeresesre es ADB-kapcsolatra tamaszkodik. Nem keruli meg a telefon biztonsagi engedelyeit, a Google-fiokhoz tartozo vedelmet vagy az Android parositasat.


1. A PROGRAM INDÍTÁSA
---------------------

SZUKSEGES PROGRAMOK
- Python 3.10 vagy ujabb
- PyQt6
- Android Platform Tools (az adb paranccsal)
- scrcpy a kepernyotukrozeshez
- Linuxos grafikus munkakornyezet

Az egyes modulok tovabbi kulso eszkozoket is hasznalhatnak. Ha valamelyik program hianyzik, az ahhoz tartozo funkcio nem feltetlenul mukodik.

ELSO INDÍTÁS
1. Tedd a projekt fajljait egy kozos konyvtarba.
2. A modulokat tartsd a modules alkonyvtarban.
3. Csatlakoztasd a telefont USB-kabellel, vagy allits be engedelyezett ADB-kapcsolatot Wi-Fi-n.
4. A telefon Fejlesztoi beallitasok menujeben engedelyezd az USB-hibakeresest.
5. USB-kapcsolat eseten oldd fel a telefont, majd fogadd el a szamitogep ADB-hibakeresesi kulcsara vonatkozo engedelykerest.
6. Inditsd el a programot a projekt konyvtarabol.

A program inditasa terminalbol:

    python3 main.py

A parancsot a projekt gyokerkonyvtaraban kell kiadni, ahol a main.py talalhato. A parancs nem a bongeszobe vagy a telefonra valo. Ha a Python 3 es a PyQt6 nincs telepitve, eloszor ezeket kell telepiteni.

ADB-KAPCSOLAT ELLENŐRZÉSE
A telefon csatlakoztatasa utan az ADB-eszkozlistaban a keszuleknek engedelyezett, „device” allapotban kell megjelennie. Ha „unauthorized” allapot lathato, oldd fel a telefont es fogadd el az engedelykerest. Ha a telefon nem lathato, ellenorizd a kabelt, az USB-kapcsolatot, az ADB telepiteset es a telefon hibakeresesi beallitasait.

Wi-Fi-s ADB-kapcsolatnal a telefon es a szamitogep halozati elerhetosege, valamint az Android parositasai es engedelyei is szuksegesek lehetnek. A Kapcsolatok modul csak felhasznaloi muvelet utan kezd csatlakozast; nem probalja megkerulni az Android engedelyezesi folyamatait.


2. A MAIN.PY – A FŐPROGRAM
--------------------------

A main.py a Redmi Control Center foablaka es kozponti vezerloje. Nem ez vegzi az osszes telefonos muveletet: a menupontok a modules konyvtar megfelelo moduljait nyitjak meg.

Fo feladatai:
- Letrehozza a foablakot, a menurendszert es a munkateruletet.
- Kezeli a modulnezetek megnyitasat es a mar megnyitott nezetek kozotti valtast.
- Kezeli a program beallitasait, az ablak meretet es a temavalasztast.
- Elmenti a beallitasokat a felhasznalo sajat konfiguracios konyvtaraba.
- A modulokat a kozos beallitasokkal tudja megnyitni.
- A fajlkezelot kulon ablakban is meg tudja nyitni.
- A kepernyotukrozes funkcioit a Kepernyo menubol teszi elerhetove.

A program temai: Alapertelmezett, Matrix, Zombie, Vampire, Barbie es Pink pussycat. A tenylegesen elerheto menupontokat a program aktualis verzioja es a telepitett modulok hatarozzak meg.

A fo beallitasfajl helye:

    ~/.config/redmi-control-center/settings.json

Egyes modulok sajat beallitasi vagy adatfajlokat is hasznalnak. A ~ jel a sajat felhasznaloi konyvtarat jelenti, ami Linuxon altalaban /home/felhasznalonev.


3. MODULOK
----------

A modulok a modules konyvtarban vannak. A foprogram ezeket a munkateruleten belul nyitja meg, ahol ezt a modul tamogatja. Egyes modulok onalloan is indithatok, de a mindennapi hasznalathoz a main.py elinditasa ajanlott.


3.1. Kapcsolatok – connections.py
---------------------------------
Az ADB-kapcsolatok ellenorzesere es kezelesere szolgal.

- Az ADB-szerver allapotanak ellenorzese es inditasa.
- USB-n csatlakozo ADB-eszkozok felismerese.
- A telefon IP-cimeinek felderitese USB-kapcsolat mellett.
- A Linuxos halozat, hotspot es utvonal-informaciok megjelenitese.
- Wi-Fi-s ADB-kapcsolat kezdemenyezese, alapertelmezetten az 5555-os porttal.
- A kapcsolat ellenorzese tenyleges ADB-paranccsal.
- Az ADB-szerver ujrainditasa vagy leallitasa.
- Reszletes naplozas es hatterszalas parancsvegrehajtas.

A Wi-Fi-s csatlakozas elott ellenorizd, hogy a telefonon az adott kapcsolati mod engedelyezve van-e. A modul nem keruli meg az Android parositas vagy hibakereses altal megkovetelt engedelyeit.


3.2. Névjegyek – contacts.py
----------------------------
A nevjegyek helyi kezeleset es a telefonos adatforrasok attekinteset tamogatja.

- ADB-eszkozfelderites es keszulekvalasztas.
- Fulre osztott felulet, keresesi es forras szerinti szurok.
- Helyi JSON-adatallomany, biztonsagos mentes es biztonsagi masolat.
- JSON-, CSV- es VCF-import/export alapok.
- Kereses, forras szerinti szures, masolas es duplikatumjelzes.
- Tarcsazo es SMS-szerkeszto, amelyek a telefon megfelelo alkalmazasat nyitjak meg.
- Szerkesztheto europai orszagkodtabla.
- Importalasi elonezet es nevjegyek egymas melletti osszehasonlitasa.
- ADB-eszkozfelderites es telefonallapot-figyeles hatterszalban.

Fontos korlat: a modul nem valosit meg teljes Android-nevjegyszinkronizalast, es a telefonon keresztuli SMS-kuldes sem resze a jelenlegi funkcioknak. A hangrogzites lehetosegeit az Android, a scrcpy es a kivalasztott backend korlatai befolyasolhatjak. A helyi nevjegyadatok a felhasznalo sajat adatkoonyvtaraba kerulnek.


3.3. Alkalmazások – applications.py
-----------------------------------
Az Android-alkalmazasok kezelesere szolgalo modul.

- ADB-eszkoz kivalasztasa es az eszkozlista frissitese.
- A telefonon talalhato alkalmazascsomagok listazasa.
- Kereses alkalmazasnev vagy csomagnev alapjan.
- Felhasznaloi alkalmazasok, rendszeralkalmazasok vagy az osszes alkalmazas megjelenitese.
- Az engedelyezett alkalmazasokra szukites.
- Alkalmazasokkal kapcsolatos tovabbi muveletek es allapotok kezelese a modul sajat feluleten.

Az alkalmazasok listaja a telefonrol, ADB-n keresztul szarmazik. A rendszeralkalmazasok es a felhasznalo altal telepitett alkalmazasok kulon szurhetok.


3.4. Eszközadatok – device_info.py
----------------------------------
A telefon azonosito- es rendszeradatainak attekintesere szolgal.

- Gyarto, marka, modell es eszkoznev.
- Android-verzio es API-szint.
- Buildazonosito es biztonsagi javitas.
- MIUI- vagy HyperOS-verzio, ha a keszulek ezeket az adatokat kozli.
- Processzor-/hardverplatformmal es ABI-val kapcsolatos adatok.
- Bootloader- es egyes rendszerbiztonsagi tulajdonsagok.

Az adatok ADB-parancsokkal kerulnek lekerdezesre. A megjelenitheto mezok az Android-verziotol es a keszulek altal kozolt tulajdonsagoktol fuggenek.


3.5. Hálózati diagnosztika – diagnostics.py
-------------------------------------------
A telefon halozati allapotanak es kapcsolati adatainak vizsgalatara szolgal.

- Aktiv halozati interfeszek es IP-utvonalak.
- Wi-Fi- es kapcsolati allapotadatok.
- DNS-cimek.
- A telefon halozati forgalmaval kapcsolatos rendszeradatok.
- A telefon egyes rendszer-tulajdonsagai.
- Diagnosztikai pillanatfelvetel es forgalmi grafikonok.

A megjelenitett adatok az Android-verziotol, a jogosultsagoktol es a rendszer altal elerhetove tett informacioktol fuggnek.


3.6. Fájlkezelő – file_manager.py
---------------------------------
Ketpaneles fajlkezelot biztosit a telefon ADB-n keresztuli fajlrendszerehez. A modul ADB-n keresztul ellenorzi a keszulekeket, es lehetoseget ad a telefon elerheto tarhelyenek bongeszesere, valamint fajlok es konyvtarak kezelesere.

A telefonon elerheto utvonalak es muveletek az Android jogosultsagi szabalyaitol fuggnek. Az ADB-kapcsolatnak engedelyezett allapotban kell lennie. A foprogrambol a fajlkezelo a munkateruleten belul vagy kulon ablakban is megnyithato.


3.7. Google-fiókok és FRP-diagnosztika – google_accounts.py
----------------------------------------------------------
A modul ADB-n keresztul vizsgal keszulekadatokat, Google Play-szolgaltatasra utalo adatokat es a rendszer altal kiadott fiokbejegyzeseket. A felulet diagnosztikai informaciokat es naplot adhat a lekerdezesekrol.

A lekert adatok az Android altal elerhetove tett rendszerkimenetbol szarmaznak, ezert nem garantalt, hogy minden keszuleken teljesek. A modul nem keruli meg az FRP-t, a Google-fiok vedelmet vagy az Android biztonsagi engedelyeit. A diagnosztika nem jelenti a zar feloldasat.


3.8. Képernyőtükrözés – screen_mirroring.py
------------------------------------------
A modul a kulso scrcpy programot inditja el, amellyel a telefon kepernyoje kulon Linuxos ablakban jelenitheto meg es – a telefon engedelyeitol fuggoen – egérrel es billentyuzettel vezerelheto.

A feluleten megadhato vagy ellenorizheto:
- Az ADB parancs eleresi utja.
- Az scrcpy parancs eleresi utja.
- A csatlakoztatott keszulek kivalasztasa.
- A tukrozes inditasa es leallitasa.
- Az ablakforgatasra vonatkozo probalkozasok es az ablakkezelo neve.
- Az aktualis allapot es a naplo.

A modul a tukrozest kulon folyamatkent inditja. A jelenlegi megvalositas nem agyazza be az scrcpy kepet a PyQt feluleten belulre. Az ablak elforgatasanak lehetosege a grafikus kornyezettol es az ablakkezelotol fugg.


3.9. Tárhelyelemző – storage.py
------------------------------
A telefon belso, felhasznalo altal elerheto tarhelyenek felmeresere szolgal. A modul ADB-n keresztul jarja be az elerheto konyvtarakat, es megprobalja osszegyujteni:

- A konyvtarstruktura adatait.
- A fajlok es konyvtarak meretet es modositas idejet, ahol ez lekerdezheto.
- Az elerhetosegi, olvashatosagi es irhatosagi informaciokat.
- A tarhely elemzesehez es a fajlokhoz tartozo muveleteket.

A felmeres a telefon elerheto tarhelyere vonatkozik; nem jelenti a teljes Android-rendszerparticio szabad bongeszeset. Egyes mappak vagy adatok hozzafereset az Android korlatozhatja.


4. BEÁLLÍTÁSOK ÉS ADATOK
------------------------
A fo beallitasok helye:

    ~/.config/redmi-control-center/settings.json

A program es a modulok ezen kivul kulon konfiguracios, helyi adat-, naplo-, export- vagy biztonsagimasolat-fajlokat is letrehozhatnak. A beallitas- es adatfajlokat ne torold addig, amig nem tudod, melyik modul hasznalja oket. Kulonosen a nevjegyek helyi adatallomanya es a biztonsagi masolatok tartalmazhatnak fontos adatokat.


5. HIBAELHÁRÍTÁS
----------------
A program nem indul el:
- Ellenorizd, hogy a Python 3 es a PyQt6 telepitve van-e.
- A projekt gyokerkonyvtarabol inditsd: python3 main.py.
- Ha hiba jelenik meg a terminalban, mentsd el a teljes hibauzenetet.

A telefon nem jelenik meg:
- Ellenorizd az USB-kabelt es a telefon USB-kapcsolatat.
- Engedelyezd az USB-hibakeresest a telefonon.
- Oldd fel a telefont es fogadd el az ADB-engedelykerest.
- A Kapcsolatok modulban frissitsd az eszkozlistat.

Az eszkoz „unauthorized” allapotu:
- A telefon kepernyojen fogadd el a szamitogep ADB-kulcsat.
- Ha az engedelykeres nem jelenik meg, ellenorizd a telefon hibakeresesi beallitasait es csatlakoztasd ujra az eszkozt.

A kepernyotukrozes nem indul:
- Ellenorizd, hogy az scrcpy telepitve van-e.
- Ellenorizd, hogy az ADB-kapcsolat engedelyezett-e.
- A kepernyotukrozes modulban frissitsd az eszkozlistat.
- Ha szukseges, ellenorizd az ADB es scrcpy eleresi utjat.

Egy modul nem mukodik:
- Nem minden funkcio mukodik ADB-kapcsolat nelkul.
- Az Android-verzio es a gyartoi korlatozasok egyes lekerdezeseket megakadalyozhatnak.
- Olvasd el a modul naplojat, es a teljes hibauzenetet hasznald a hiba beazonositasahoz.


6. BIZTONSÁG ÉS ADATVÉDELEM
---------------------------
- Csak olyan telefont csatlakoztass, amelynek kezelesere jogosult vagy.
- Az Android hibakeresesi engedelykereseit a telefonon kell elfogadni.
- A program nem helyettesiti a Google-fiokot, a telefon jelszavat vagy a gyarto biztonsagi ellenorzeseit.
- A helyi nevjegyek, naplok, exportok es biztonsagi masolatok szemelyes adatokat tartalmazhatnak. Ezeket kezeld bizalmasan.
- Wi-Fi-s ADB-t csak megbizhato halozaton hasznalj, es kapcsold ki, ha mar nincs ra szukseg.


7. PROJEKTSTRUKTÚRA
-------------------
A fo fajlok es modulok:

    main.py
    modules/
        applications.py
        connections.py
        contacts.py
        device_info.py
        diagnostics.py
        file_manager.py
        google_accounts.py
        screen_mirroring.py
        storage.py

A main.py a kozponti grafikus alkalmazas. A modules konyvtar fajljai kulon funkcioegysegek. Mukodesuk a Python-, PyQt6-, ADB-, scrcpy- es Android-kornyezettol fugg.


8. KÖZREMŰKÖDÉS ÉS HIBAJELENTÉS
-------------------------------
Hibajelenteskor add meg:
- A Linux-disztribucio es verzio adatait.
- A Python, PyQt6, ADB es – ha erintett – az scrcpy verziojat.
- A telefon modelljet es Android-verziojat.
- A hiba pontos lepeseket es a teljes hiba- vagy naplouzenetet.

Jelszot, Google-fiokhoz tartozo titkos adatot, telefonszamot vagy mas szemelyes adatot ne tegyel nyilvanos hibajegybe.


LICENC
------
A projekt licencet a repository LICENSE fajlja hatarozza meg, ha van ilyen. Ha nincs LICENSE fajl, a forraskod felhasznalasi feltetelei nincsenek egyertelmuen megadva.
