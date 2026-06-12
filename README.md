# Anime Processor – zpracování anime

![Anime Processor – zpracování anime](https://padik.eu/wp-content/uploads/2026/05/595940531-60e7046b-40cb-43e3-82ff-1bf835fba909.png)

# 🎬 Anime Subtitle Processor for Jellyfin

[](https://github.com/padikcz/Anime-processor-#-anime-subtitle-processor-for-jellyfin)

## 📖 Popis

[](https://github.com/padikcz/Anime-processor-#-popis)

Tento program slouží k automatickému zpracování titulků pro anime tak, aby byly plně kompatibilní se systémem Jellyfin. Nenačítá ani negeneruje titulky ze zvuku, ale pracuje s již existujícími titulky, které exportuje, přeloží a správně uloží.

Program využívá API služby LibreTranslate pro automatický překlad titulků. Výchozí jazyk překladu je **čeština**. Pro správnou funkčnost je nutné, aby váš LibreTranslate server měl stažený a aktivní český jazykový model.

Ve výchozím nastavení je program navržen tak, aby zpracovával anime releasy ze **SubsPlease**, což ho činí ideálním pro automatizované workflow.

Program obsahuje také přehledné **webové rozhraní (Web UI)**, které umožňuje snadné nastavení, správu a spouštění procesů přímo z prohlížeče.

## ⚠️ Upozornění

[](https://github.com/padikcz/Anime-processor-#%EF%B8%8F-upozorn%C4%9Bn%C3%AD)

- Způsob, jakým získáte anime soubory, je **čistě na vás a na vaší zodpovědnosti**

- Program pracuje **pouze se soubory, které mu uživatel poskytne** a nijak neřeší jejich původ

## ✨ Hlavní funkce

[](https://github.com/padikcz/Anime-processor-#-hlavn%C3%AD-funkce)

- 📤 Export existujících titulků z anime souborů

- 🌍 Automatický překlad do češtiny pomocí LibreTranslate API

- ⏱️ Zachování časování a formátu titulků (SRT, ASS)

- 🧠 Správné pojmenování podle standardů Jellyfinu

- 📁 Automatické uložení do správné složky k videu

- 🌐 Web UI pro jednoduché ovládání

- 🎌 Optimalizováno pro SubsPlease releasy

- ⚡ Zjednodušení správy titulků pro anime knihovny

## ⚙️ Požadavky

[](https://github.com/padikcz/Anime-processor-#%EF%B8%8F-po%C5%BEadavky)

- Běžící LibreTranslate server

- Stažený český jazyk v LibreTranslate

- Anime soubory s existujícími titulky

- (Volitelné) Jellyfin server

## 🚀 Jak to funguje

[](https://github.com/padikcz/Anime-processor-#-jak-to-funguje)

- Program načte anime soubor s titulky

- Exportuje existující titulky

- Přeloží je pomocí LibreTranslate API

- Zachová časování a formát

- Přejmenuje soubor podle standardů Jellyfinu

- Uloží titulky do správné složky

## 🐳 Docker Setup

[](https://github.com/padikcz/Anime-processor-#-docker-setup)

Program můžeš snadno spustit pomocí Dockeru:

version: "3.9"

services:
  anime-processor:
    container_name: anime-processor
    image: padikcz/mini-anime-processor:latest
    ports:
      - "5001:5001"
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 256M
    volumes:
      - /DATA/Downloads/sp:/media/in
      - /mnt/Storage1/jellyfin:/media/out

### 📂 Složky

[](https://github.com/padikcz/Anime-processor-#-slo%C5%BEky)

- /media/in → vstup (např. SubsPlease downloady)

- /media/out → výstup (např. Jellyfin knihovna)

## 🧊 CasaOS

[](https://github.com/padikcz/Anime-processor-#-casaos)

Tento projekt je testovaný a provozovaný na CasaOS:

name: refreshing_elina
services:
  main_app:
    cpu_shares: 50
    command: []
    container_name: anime-processor
    deploy:
      resources:
        limits:
          memory: 256M
    hostname: anime-processor
    image: padikcz/mini-anime-processor:latest
    ports:
      - target: 5001
        published: "5001"
        protocol: tcp
    restart: unless-stopped
    volumes:
      - type: bind
        source: /DATA/Downloads/sp
        target: /media/in
      - type: bind
        source: /mnt/Storage1/jellyfin
        target: /media/out
    network_mode: bridge

x-casaos:
  author: self
  category: self
  port_map: "5001"
  scheme: http
  store_app_id: refreshing_elina
  title:
    custom: Anime processor

## 🖥️ Webové rozhraní

[](https://github.com/padikcz/Anime-processor-#%EF%B8%8F-webov%C3%A9-rozhran%C3%AD)

Po spuštění je dostupné na:

[http://localhost:5001](http://localhost:5001/)

## 📌 Poznámky

[](https://github.com/padikcz/Anime-processor-#-pozn%C3%A1mky)

- Program **negeneruje titulky ze zvuku (žádný speech-to-text)**

- Funguje pouze s již existujícími titulky

- Ideální pro automatizované anime knihovny

## ❤️ Pro koho je to

[](https://github.com/padikcz/Anime-processor-#%EF%B8%8F-pro-koho-je-to)

- Uživatelé Jellyfinu

- Fanoušci anime 🎌

- Lidé, co chtějí automatizovat titulky

---

[Přečíst článek na webu](https://padik.eu/anime-processor/)
