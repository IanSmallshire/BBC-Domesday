# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project is to attempt to decode the datasets from the BBC Domesday project Laserdiscs into a more usable form. 
The Walk and Gallery subsystem provides "surrogate walks" - an early form of virtual reality navigation through 
photographic environments stored on LaserDisc. Users can navigate a graph of connected locations, turning on the spot 
in 8 directions and moving forward through connected nodes.
The Gallery is the entry point - a virtual museum space from which users can access various "walks" into 
real-world locations photographed from the National disc.

The initial stages of the project will be to understand the data structures and then to implement a more modern storage of the same data with round trip preservation testing.

It would be good to get a python script that can convert the data format for the walks into a queryable structure and then render a basic navigation user interface within a web browser.



## Project Setup

This is a Python project using Python 3.10 with a virtual environment in `.venv/`.
The following folders contain data required 

| folder/files                                              | usage                                                                 |
|-----------------------------------------------------------|-----------------------------------------------------------------------|
| [NationalA](data/NationalA/VFS)                           | Datafiles for the National disc Side A                                |
| [jpgimg](data/NationalA/jpgimg)                           | jpegs for frames with sub folders for each 1000 frames.               |
| [CommN](data/CommN)                                       | Similar to the [NationalA](data/NationalA) folder for community North |
| [CommS](data/CommS)                                       | Similar to the [NationalA](data/NationalA) folder for community South |
| [src](build/src)                                          | Contains the BCPL Source code for the original system                 |
| [nationalA.768x576i25.mp4](data/nationalA.768x576i25.mp4) | mp4 video of each of the frames frame aligned to allow audio          |
| [nationalB.768x576i25.mp4](data/nationalB.768x576i25.mp4) | mp4 video of the content on side B of the National Disc               |
| [communityN.768x576i25.mp4](data/communityN.768x576i25.mp4) | Community North video file |
| [communityS.768x576i25.mp4](data/communityS.768x576i25.mp4) | Community North video file |

Other related projects

| URL | Description                                                                                        |
|-----|----------------------------------------------------------------------------------------------------|
| https://github.com/simoninns/VP415Emu | This is an emulator for the connected VP415 Laserdisc player and will contain references to FCODES |
| https://github.com/simoninns/acorn-aiv-concise-user-guide | This is an updated user guide for the original system                                              |
| https://github.com/simoninns/OpenAIV | This is a partial implimentation for a tool to naviagte the datasets                               |


None of the datafiles can be pushed to git.

### Installation


## Commands


## Code Style

- Line length: 120 characters (Ruff)
- Python 3.10 features encouraged (type hints, match statements, etc.)


## Package Structure


## Practices

- Strongly prefer test-driven development or at least test-first. All new features and bug fixes should have associated tests.
- Respect the layering and architecture boundaries between packages:

