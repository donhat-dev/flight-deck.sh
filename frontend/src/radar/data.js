/**
 * The radar's data layer.
 *
 * Everything the drawing needs is DERIVED SERVER-SIDE — a blip's ring, whether it
 * moved in or out, how old its newest evidence is. None of that is re-implemented
 * here, and that is deliberate: the same derivation living in two languages is the
 * shape of bug where the table says one thing and the circle says another.
 *
 * `geometry.js` stays pure and knows nothing about fetching, so the placement math
 * remains unit-testable without a server.
 */
import { useCallback, useEffect, useState } from "react";

import { get, post } from "../api.js";

/**
 * One fetch, with the three states a data-driven surface has to be able to show.
 *
 * `loading` starts true rather than false. Starting false makes the first paint an
 * empty state, which reads as "there is nothing" for as long as the request takes —
 * a lie the reader cannot tell from the truth.
 */
function useFetch(path, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const load = useCallback(() => {
    let live = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    get(path)
      .then((data) => live && setState({ data, error: null, loading: false }))
      .catch((error) => live && setState({ data: null, error, loading: false }));
    return () => { live = false; };
  }, [path]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, deps ?? [path]);
  return { ...state, reload: load };
}

export const useRadars = () => {
  const r = useFetch("/api/radar/radars");
  return { ...r, radars: r.data?.radars ?? null };
};

export const useRadar = (slug) => {
  const r = useFetch(`/api/radar/${encodeURIComponent(slug)}`);
  return { ...r, board: r.data };
};

export const useBlip = (slug, num) => {
  const r = useFetch(`/api/radar/${encodeURIComponent(slug)}/blips/${num}`);
  return { ...r, blip: r.data };
};

/**
 * Move a blip.
 *
 * `evidence` is a required argument with no default, mirroring the API: a move that
 * cannot cite anything is refused at the boundary rather than accepted and then
 * quietly unauditable.
 */
export function moveBlip(slug, num, { ring, period, why, evidence }) {
  return post(`/api/radar/${encodeURIComponent(slug)}/blips/${num}/moves`,
              { ring, period, why, evidence });
}

/** The radar a fresh page opens on: whichever the API lists first. */
export const OPEN_RADAR = "subscription-migration";
