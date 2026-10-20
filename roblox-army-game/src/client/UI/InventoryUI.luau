--!strict
--[[
	InventoryUI  (STUB written by the lead — GEAR replaces the body, keeps the signature)
	See docs/ARCHITECTURE.md §4.5.
]]

local InventoryUI = {}

export type OpenOptions = {
	Title: string?,
	ActionText: string?,
	OnAction: (() -> ())?,
	OnClose: (() -> ())?,
}

function InventoryUI.Open(opts: OpenOptions): () -> ()
	if opts.OnAction then
		opts.OnAction()
	end
	return function() end
end

return InventoryUI
