# imgtext — képen lévő felirat cseréje / fordítása

Egy képen lévő szöveg (meme-felirat, tábla, poszter felirat) **magyarra fordítása vagy
lecserélése** úgy, hogy az eredmény ne látsszon photoshopnak. Csak `PIL` + `numpy`
(**nincs OpenCV, nincs scipy**, nincs inpainting lib).

## Miért így

- A szövegdoboz (pl. fehér buborék) alakját nem rajzoljuk újra: abból **csak a betűk
  tűnnek el**, a doboz és a kép többi része pixelre ugyanaz marad.
- A fontot megmérjük és utánozzuk (a legtöbb meme-felirat keskenyített, ezért pl.
  Roboto Condensed + vízszintes összenyomás kell).
- Ellenőrzés diffel: a két szerkesztett területen kívül **0** változott pixel.

## Telepítés / előfeltétel

```bash
python3 -c "import PIL, numpy"          # kell: Pillow, numpy
fc-list | grep -iE "roboto condensed|kalam"
```

Fontok (ha nincsenek):

```bash
mkdir -p ~/.local/share/fonts && cd ~/.local/share/fonts
curl -sSLo RobotoCondensed-VF.ttf "https://raw.githubusercontent.com/google/fonts/main/ofl/robotocondensed/RobotoCondensed%5Bwght%5D.ttf"
curl -sSLo Kalam-Bold.ttf          "https://raw.githubusercontent.com/google/fonts/main/ofl/kalam/Kalam-Bold.ttf"
fc-cache -f
```

## Használat

### 1) Mérés — hol vannak a sorok, mekkora a betű

```bash
python3 imgtext.py probe kep.jpg --region 157,6,1049,292
```

Kimenet:

```
line 1: y 27-100  h=74  x 219-985  w=767
line 2: y 120-176 h=57  x 187-1016 w=830
line 3: y 211-285 h=75  x 350-853  w=504
legnagyobb tintamagasság: 75px  (em ~ 103px)
középvonal x: 603.0
```

Ebből jön a `baselines` (a leszálló nélküli sor alsó éle = baseline), a `center_x`
és a fontméret. A `probe` a régió szélét érintő sötét foltokat (a kép háttere)
kihagyja, ezért nem keveri bele a poszter/díszlet sötét részeit.

**Fontméret:** `cap_height ≈ 0.72 × size` (Roboto Condensed: 57 px cap → size 80).
Ha az eredeti keskenyebb, mint a választott font, megmérjük az arányt
(`szövegszélesség / em`) és a `squeeze` értékkel visszaadjuk — pl. Arial-arány 13,1 em,
az eredeti 9,8 em → Roboto Condensed (10,0 em) + `squeeze: 0.85` adta vissza a kinézetet.

### 2) Csere — `job.json`

Lásd `example-job.json` (ez a 1932-es propaganda-meme receptje, bájtra ugyanazt adja):

```json
{
  "src": "bemenet.jpg", "out": "kimenet.jpg", "quality": 96,
  "caption": {
    "region": [157, 6, 1049, 292],
    "lines": ["1932-ben a német propaganda", "azt mutatta, milyen lesz", "Franciaország 100 év múlva."],
    "baselines": [84, 177, 268],
    "center_x": 602.5,
    "font": "/home/gma/.local/share/fonts/RobotoCondensed-VF.ttf",
    "size": 80, "weight": 400, "squeeze": 0.85, "color": [12, 12, 12]
  },
  "sign": {
    "box": [860, 680, 1120, 870],
    "quad": [[904, 753], [1074, 711], [1080, 823], [899, 838]],
    "border_width": 7, "angle": 13.9, "center": [991, 781],
    "lines": [["TILOS", -28], ["ETETNI", 26]],
    "font": "/home/gma/.local/share/fonts/Kalam-Bold.ttf",
    "size": 30, "tracking": 4, "color": [78, 66, 46]
  }
}
```

```bash
python3 imgtext.py run example-job.json
```

- `caption` = a szövegdoborban lévő felirat cseréje (a `region` a doboz belső területe).
- `sign` = kézírásos/festett tábla: a `quad` a tábla négy sarka (TL, TR, BR, BL),
  a `box` a munkaterület, `border_width` a megtartott keretsáv szélessége (px).
- `lines` a táblánál: `[["SOR", dy], ...]`, ahol `dy` a tábla közepéhez képest mért eltolás.

### 3) Ellenőrzés (mindig!)

```bash
python3 - <<'EOF'
from PIL import Image
import numpy as np
o = np.array(Image.open("eredeti.jpg").convert("RGB")).astype(int)
n = np.array(Image.open("kimenet.jpg").convert("RGB")).astype(int)
d = np.abs(o-n).sum(axis=2) > 12
mask = np.zeros(d.shape, bool)
mask[0:295, 150:1055] = True     # a szerkesztett régiók
mask[690:870, 860:1120] = True
print("régiókon kívüli változás:", int((d & ~mask).sum()))   # 0 kell
EOF
```

## Hogyan működik (a trükkök)

1. **Régi szöveg törlése fehér dobozból.** A betűk „lyukak” a fehér régióban.
   A buborékot nem flood-fillel keressük (a poszter fehér fala összeér vele), hanem:
   sötét magok (`lum < 170`) 8-összefüggő komponensei; a régió szélét érintő
   komponensek = háttér/poszter → kihagyjuk; majd 4 px dilate + `lum < 254` → fehérre.
   A JPEG-halo miatt kell a tágítás és a magasabb küszöb, különben szellemkép marad.
2. **Baseline-ok megtartása.** A régi sorok baseline-jaira írunk (`anchor="ms"`),
   így a szövegblokk pontosan ott van, ahol volt.
3. **Vízszintes összenyomás.** A sort külön layerre rendereljük, `resize(width*squeeze)`,
   majd középre illesztjük — így kapjuk vissza a „keskenyített” eredeti kinézetet.
4. **Tábla újraépítése.** A papír hátteret normalizált konvolúcióval állítjuk elő
   (`box_blur(I*w)/box_blur(w)`, ahol `w = ~sötét`), így a betűk eltűnnek, de a
   színátmenet/árnyék megmarad. Az `alpha` a tábla poligonja **mínusz** egy
   `border_width` széles keretsáv → a kézírásos keret érintetlen marad.
   A feliratot a tábla dőlésszögével forgatva írjuk rá (kézírásos font: Kalam Bold).
5. **Szín.** A szöveg színét az eredetiből mérjük (itt ~`(12,12,12)`), a táblánál
   a tinta ~`(78,66,46)`.

## Ismert csapdák

- **Túl szűk törlési régió** → a leszállók (y, g, p) elvágottan megmaradnak; a régiót
  a legalsó leszálló alá kell tenni, de a doboz alatti posztert nem szabad belevenni.
  A „szélét érintő komponens kimarad” szabály pont ezt kezeli.
- **`Image.fromarray(float)`** → `TypeError: Cannot handle this data type`; töltsd
  `np.uint8`-ra.
- **PIL GaussianBlur** csak `L`/`RGB` módot ismer (float `F` nem) → saját `box_blur`.
- **Forgatás iránya**: a `rotate()` pozitív szöge **órajárással ellentétes**; kézzel
  ellenőrizd a dőlést, mert az ellenkező előjelű szöveg „lecsúszik” a tábláról.
- **Dupla kompozitálás / rossz offset** esetén a szöveg a kép bal felső sarkában is
  megjelenik — a `probe`/diff ezt azonnal megfogja.

## Referencia-eset

`~/Pictures/862480971_18634046542047018_3274318619214845719_n.jpg` →
`..._hu.jpg` (teljes fordítás) és `..._hu_caption.jpg` (csak a felső felirat).
