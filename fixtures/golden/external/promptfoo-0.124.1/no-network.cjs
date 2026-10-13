// Guard network entry points before loading the pinned CLI. No destination logging.
const fs = require('node:fs');
const attempts=[];
function blocked(name) { return function () { attempts.push(name); throw new Error('C35_NETWORK_DISABLED'); }; }
require('node:net').Socket.prototype.connect = blocked('net.Socket.connect');
require('node:tls').connect = blocked('tls.connect');
require('node:dns').lookup = blocked('dns.lookup');
require('node:dns').resolve = blocked('dns.resolve');
require('node:dns').promises.lookup = blocked('dns.promises.lookup');
require('node:dgram').Socket.prototype.send = blocked('dgram.Socket.send');
globalThis.fetch = blocked('fetch');
process.on('exit', () => fs.writeFileSync(process.env.C35_NETWORK_LOG,JSON.stringify(attempts,null,2)));
