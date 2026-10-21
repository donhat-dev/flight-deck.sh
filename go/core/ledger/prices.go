package ledger

import (
	"database/sql"
	"time"

	"flightdeck/core/pricing"
)

const pricesSchema = `
CREATE TABLE IF NOT EXISTS price_models (
  prefix          TEXT NOT NULL,
  valid_from      TEXT NOT NULL DEFAULT '',
  input           REAL NOT NULL,
  output          REAL NOT NULL,
  cache_read_mult REAL NOT NULL,
  write_5m_mult   REAL NOT NULL,
  write_1h_mult   REAL NOT NULL,
  fast_input      REAL,
  fast_output     REAL,
  note            TEXT,
  PRIMARY KEY (prefix, valid_from)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS price_geo (geo TEXT PRIMARY KEY, mult REAL NOT NULL) WITHOUT ROWID;
`

// seedPrices writes the shipped table once per ledger. The meta flag, not an
// empty table, decides: an owner who imports an empty table keeps it empty.
func seedPrices(db *sql.DB) error {
	tx, err := db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var n int
	if err := tx.QueryRow(`SELECT count(*) FROM meta WHERE key='prices_seeded'`).Scan(&n); err != nil {
		return err
	}
	if n > 0 {
		return nil
	}
	if err := ReplacePrices(tx, pricing.Defaults()); err != nil {
		return err
	}
	if _, err := tx.Exec(`INSERT INTO meta (key, value) VALUES ('prices_seeded', ?)`,
		time.Now().UTC().Format(time.RFC3339)); err != nil {
		return err
	}
	return tx.Commit()
}

// Prices reads the stored table.
func Prices(q Querier) (pricing.Config, error) {
	c := pricing.Config{Models: []pricing.Row{}, Geo: map[string]float64{}}
	rows, err := q.Query(`SELECT prefix, valid_from, input, output, cache_read_mult, write_5m_mult,
		write_1h_mult, fast_input, fast_output, COALESCE(note,'') FROM price_models ORDER BY prefix, valid_from`)
	if err != nil {
		return c, err
	}
	defer rows.Close()
	for rows.Next() {
		var r pricing.Row
		var fi, fo sql.NullFloat64
		if err := rows.Scan(&r.Prefix, &r.ValidFrom, &r.Input, &r.Output, &r.CacheReadMult,
			&r.Write5mMult, &r.Write1hMult, &fi, &fo, &r.Note); err != nil {
			return c, err
		}
		if fi.Valid && fo.Valid {
			r.FastInput, r.FastOutput = &fi.Float64, &fo.Float64
		}
		c.Models = append(c.Models, r)
	}
	if err := rows.Err(); err != nil {
		return c, err
	}
	geo, err := q.Query(`SELECT geo, mult FROM price_geo`)
	if err != nil {
		return c, err
	}
	defer geo.Close()
	for geo.Next() {
		var g string
		var m float64
		if err := geo.Scan(&g, &m); err != nil {
			return c, err
		}
		c.Geo[g] = m
	}
	return c, geo.Err()
}

// PriceTable is the stored table, ready to price usage.
func PriceTable(q Querier) (*pricing.Table, error) {
	c, err := Prices(q)
	if err != nil {
		return nil, err
	}
	return pricing.New(c)
}

// ReplacePrices swaps the whole table for c, after checking it.
func ReplacePrices(tx *sql.Tx, c pricing.Config) error {
	n, err := c.Normalize()
	if err != nil {
		return err
	}
	if _, err := tx.Exec(`DELETE FROM price_models`); err != nil {
		return err
	}
	if _, err := tx.Exec(`DELETE FROM price_geo`); err != nil {
		return err
	}
	for _, r := range n.Models {
		if _, err := tx.Exec(`INSERT INTO price_models (prefix, valid_from, input, output, cache_read_mult,
			write_5m_mult, write_1h_mult, fast_input, fast_output, note) VALUES (?,?,?,?,?,?,?,?,?,?)`,
			r.Prefix, r.ValidFrom, r.Input, r.Output, r.CacheReadMult, r.Write5mMult, r.Write1hMult,
			r.FastInput, r.FastOutput, null(r.Note)); err != nil {
			return err
		}
	}
	for g, m := range n.Geo {
		if _, err := tx.Exec(`INSERT INTO price_geo (geo, mult) VALUES (?,?)`, g, m); err != nil {
			return err
		}
	}
	return nil
}

// SetPrice adds or replaces the row with r's prefix and valid_from, checking
// the resulting table as a whole.
func SetPrice(tx *sql.Tx, r pricing.Row) error {
	c, err := Prices(tx)
	if err != nil {
		return err
	}
	kept := []pricing.Row{}
	for _, x := range c.Models {
		if x.Prefix != r.Prefix || x.ValidFrom != r.ValidFrom {
			kept = append(kept, x)
		}
	}
	c.Models = append(kept, r)
	return ReplacePrices(tx, c)
}
