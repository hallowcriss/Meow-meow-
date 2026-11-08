# Controller Replay

Record your game controller and replay every stick movement, trigger pull and button press.

There are two ways to use it:

| | Desktop recorder (recommended) | Website only |
| --- | --- | --- |
| How | Run `recorder.py`, then open http://localhost:8765 | Open `index.html` in Chrome, Edge or Firefox |
| Records while a game is fullscreen | Yes, it runs in the background | No, the page has to stay visible |
| Records console games | No, see below | No |
| Where recordings go | `.json` files in `controller-viewer/recordings/` | Your browser's storage |

## Desktop recorder

You need Python 3.9 or newer ([python.org](https://www.python.org/downloads/); on Windows, tick
"Add python.exe to PATH" during install).

**Easiest:** download this folder and double-click the launcher:

- Windows: `Start recorder (Windows).bat`
- Mac: `Start recorder (Mac).command` (the first time, right-click it and choose Open)

**Or from a terminal:**

```bash
pip install pygame
python recorder.py
```

Your browser opens http://localhost:8765, the Controller Replay website, connected to the recorder.
Leave the recorder's window open while you play. Closing it stops the recorder.

**Start or stop a recording at any time:**

- hold **Back + Start** on the controller for half a second (View + Menu on Xbox pads, Share + Options
  on PlayStation pads). This works in the middle of a game, and the shortcut itself is cut out of the recording.
- press **Record** or the **R** key on the website
- press **Enter** in the recorder's window

When you stop, the recording is saved and opens on the website. Everything you've recorded is listed
under **Your recordings**. You can also open the `.json` files on the website-only version or send them to someone.

Options:

```
python recorder.py --port 9000        # use a different port
python recorder.py --dir D:\captures  # save recordings somewhere else
python recorder.py --no-browser       # don't open the website automatically
python recorder.py --demo             # try it with a pretend controller
python recorder.py --list             # show which controllers are connected
```

The website only listens on your own computer (`localhost`); nobody else on your network can reach it.

## Website only

Open `index.html` in Chrome, Edge or Firefox. Connect a controller and press any button on it, then press
Record, press R, or hold Back + Start. Recordings are kept in that browser. Use **Save .json** to keep a file.

Browsers stop reporting controllers to pages that aren't visible, so this version only records while the
page is on screen. Use the desktop recorder for games that take over the screen.

## Console games

Neither version can read a controller that's connected to a console. To record console play, connect the
controller to your computer instead, for example with remote play (PS Remote Play, Xbox app), or stream the
console to your PC and play from there, then run the desktop recorder.

## Recording format

Each recording is JSON: `samples` is a list of `{ "t": milliseconds, "a": [leftX, leftY, rightX, rightY],
"b": [17 button values] }` at about 60 per second, in the browser's standard controller layout
(A, B, X, Y, LB, RB, LT, RT, Back, Start, L3, R3, Up, Down, Left, Right, Home). Sticks run from −1 to 1
(down is positive Y); buttons and triggers from 0 to 1.
