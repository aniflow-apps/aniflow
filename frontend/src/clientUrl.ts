/**
 * The address field follows the client dropdown (build 236).
 *
 * All three torrent clients share one address field, so switching the dropdown kept the old client's address: a
 * Transmission test answered 404 because the field still said http://deluge:8112. When the address ends in the
 * port the *previous* client listens on by default, the port becomes the new client's. Anything else - a port
 * somebody chose, no port, a proxy path - is left exactly as it is.
 */
export type TorrentClient = "qbittorrent" | "deluge" | "transmission";

/** The port each client's web interface or RPC listens on out of the box. */
export const DEFAULT_PORTS: Record<TorrentClient, number> = {
  qbittorrent: 8080,
  deluge: 8112,
  transmission: 9091,
};

export function addressForClient(address: string | null | undefined, from: TorrentClient, to: TorrentClient): string {
  const text = (address ?? "").trim();
  if (!text || from === to) return address ?? "";
  const found = /^(https?:\/\/[^/:]+):(\d+)(\/.*)?$/i.exec(text);
  if (!found || Number(found[2]) !== DEFAULT_PORTS[from]) return address ?? "";
  return `${found[1]}:${DEFAULT_PORTS[to]}${found[3] ?? ""}`;
}
