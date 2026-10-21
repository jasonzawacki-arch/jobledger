// Set-up for the Google Play build (wrapped by Capacitor in Req. 2): the in-page engine, data kept
// on the device. No server connection (N-5) and no Drive sync in v1 (Req. 6).
import { Engine, install } from './local';
import { keepData, load, save } from './store';

(window as unknown as { __JL_MODE__: string }).__JL_MODE__ = 'play';
install(load().then((d) => new Engine(d, { save: (x) => { void save(x); keepData(); } })));
