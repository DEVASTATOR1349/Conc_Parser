#!/usr/bin/env python3
"""socks5_server.py — лёгкий SOCKS5-прокси (без внешних зависимостей).
Запуск: python3 socks5_server.py &
Порт: 1080
"""

import asyncio, socket, struct, logging, sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s SOCKS5 %(message)s")
log = logging.getLogger("socks5")

async def handler(reader, writer):
    peername = writer.get_extra_info('peername')
    try:
        # SOCKS5 handshake
        ver, nmethods = struct.unpack('!BB', await reader.readexactly(2))
        await reader.readexactly(nmethods)
        writer.write(struct.pack('!BB', 5, 0))  # NO AUTH
        await writer.drain()

        # Request
        ver, cmd, rsv, atyp = struct.unpack('!BBBB', await reader.readexactly(4))
        if ver != 5:
            writer.close(); return

        # Address
        if atyp == 1:  # IPv4
            host = socket.inet_ntoa(await reader.readexactly(4))
        elif atyp == 3:  # Domain
            length = (await reader.readexactly(1))[0]
            host = (await reader.readexactly(length)).decode()
        elif atyp == 4:  # IPv6
            host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
        else:
            writer.close(); return

        port = struct.unpack('!H', await reader.readexactly(2))[0]
        log.info("%s:%d <- %s:%d", host, port, *peername)

        if cmd == 1:  # CONNECT
            try:
                remote_reader, remote_writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=30)
            except Exception as e:
                log.warning("CONNECT %s:%d FAILED: %s", host, port, e)
                writer.close(); return

            writer.write(struct.pack('!BBBB', 5, 0, 0, 1)
                         + struct.pack('!4sH', socket.inet_aton('0.0.0.0'), 0))
            await writer.drain()

            async def pipe(src, dst, label):
                try:
                    while True:
                        data = await src.read(65536)
                        if not data: break
                        dst.write(data)
                        await dst.drain()
                except: pass
                finally:
                    try: dst.close()
                    except: pass

            await asyncio.gather(
                pipe(reader, remote_writer, f"{host}:{port} up"),
                pipe(remote_reader, writer, f"{host}:{port} dn"),
            )
        else:
            log.info("CMD=%d not supported", cmd)
            writer.close()
    except asyncio.IncompleteReadError:
        pass
    except Exception as e:
        log.debug("Error: %s", e)
    finally:
        try: writer.close()
        except: pass


async def main(port=1080):
    server = await asyncio.start_server(handler, '0.0.0.0', port)
    log.info("SOCKS5 proxy listening on port %d", port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 1080
    asyncio.run(main(port))
