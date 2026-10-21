-- Wrap every top-level table in <div class="table-wrap">.
--
-- pandoc has no option for this and the token sheet needs the element: a table is
-- allowed to be wider than the prose column it sits in, and a block cannot exceed
-- its containing block without a parent to carry the negative margins.
--
-- Written as a `Pandoc` function rather than `Table` or `Blocks` on purpose. Those
-- are re-entered while pandoc walks the tree the filter just returned, so a Div
-- holding a Table gets wrapped again, and again. `Pandoc` runs once, over the
-- finished document.
--
-- Top-level only, matching the sheet's own `.doc >` scoping: a table nested inside a
-- component div belongs to that component's layout, not to the page's.
function Pandoc(doc)
  local out = {}
  for _, block in ipairs(doc.blocks) do
    if block.t == "Table" then
      table.insert(out, pandoc.Div({ block }, pandoc.Attr("", { "table-wrap" })))
    else
      table.insert(out, block)
    end
  end
  return pandoc.Pandoc(out, doc.meta)
end
