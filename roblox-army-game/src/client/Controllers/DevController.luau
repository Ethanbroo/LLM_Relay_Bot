--!strict
--[[
	DevController (Studio only)
	Adds a "DEV" button to the HUD while testing in Roblox Studio. It opens a
	panel of shortcuts handled by server/Services/DevService. Nothing is shown
	in the live game.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Client = script.Parent.Parent
local Theme = require(Client.UI.Theme)

local Shared = ReplicatedStorage:WaitForChild("Shared")
local Net = require(Shared.Net)

local P = Theme.Palette

local DevController = {}
DevController.Priority = -10

local registry: { [string]: any } = {}

local function run(action: string, ...: any)
	local result = Net.Request(action, ...)
	if result.ok then
		Theme.Toast("DEV: done", "success")
	else
		Theme.Toast("DEV: " .. tostring(result.err), "error")
	end
	return result
end

local function openPanel()
	local _, content, close = Theme.Panel({
		Name = "DevPanel",
		Title = "Studio test tools",
		HeaderColor = P.ButtonGrey,
		HeaderDarkColor = P.ButtonGreyDark,
		Size = UDim2.fromOffset(560, 470),
	})
	local grid = Instance.new("UIGridLayout")
	grid.CellSize = UDim2.fromOffset(250, 58)
	grid.CellPadding = UDim2.fromOffset(10, 10)
	grid.HorizontalAlignment = Enum.HorizontalAlignment.Center
	grid.SortOrder = Enum.SortOrder.LayoutOrder
	grid.Parent = content

	type DevButton = { text: string, color: Color3, onClick: () -> () }
	local buttons: { DevButton } = {
		{ text = "+ $1,000", color = P.ButtonGreen, onClick = function()
			run("DevGiveCash", 1000)
		end },
		{ text = "+ $100,000", color = P.ButtonGreen, onClick = function()
			run("DevGiveCash", 100000)
		end },
		{ text = "+ $10,000,000", color = P.ButtonGreen, onClick = function()
			run("DevGiveCash", 10000000)
		end },
		{ text = "Beat current barrier", color = P.ButtonRed, onClick = function()
			run("DevBeatBarrier")
		end },
		{ text = "Wood chest", color = P.Wood, onClick = function()
			local result = run("DevChest", "wood")
			if result.ok and result.item then
				close()
				require(Client.UI.ChestUI).Play("wood", result.item)
			end
		end },
		{ text = "Gold chest", color = P.Coin, onClick = function()
			local result = run("DevChest", "gold")
			if result.ok and result.item then
				close()
				require(Client.UI.ChestUI).Play("gold", result.item)
			end
		end },
		{ text = "2x cash 15 min", color = P.OfferPurple, onClick = function()
			run("DevBoost", "x2_15")
		end },
		{ text = "Time skip 4h", color = P.OfferPurple, onClick = function()
			run("DevBoost", "skip_4")
		end },
		{ text = "Start a run", color = P.CardOrange, onClick = function()
			close()
			local runner = registry.RunnerController
			if runner and runner.OpenPrepare then
				runner:OpenPrepare()
			end
		end },
		{ text = "Reset my progress", color = P.ButtonGreyDark, onClick = function()
			close()
			Theme.Confirm({
				Title = "Reset progress?",
				Body = "Wipes your test save back to a brand-new player.",
				ConfirmText = "Reset",
				ConfirmColor = P.ButtonRed,
				OnConfirm = function()
					run("DevReset")
				end,
			})
		end },
	}
	for index, spec in buttons do
		Theme.Button({
			Text = spec.text,
			Color = spec.color,
			LayoutOrder = index,
			MaxTextSize = 22,
			ZIndex = 14,
			OnClick = spec.onClick,
			Parent = content,
		})
	end
end

function DevController:Init(reg: { [string]: any })
	registry = reg
end

function DevController:Start()
	if not RunService:IsStudio() then
		return
	end
	local hud = registry.HUDController
	if hud then
		hud:AddMenuButton({
			Id = "Dev",
			Title = "DEV",
			Icon = "🛠️",
			Order = 99,
			HeaderColor = P.ButtonGrey,
			HeaderDarkColor = P.ButtonGreyDark,
			OnClick = openPanel,
		})
	end
end

return DevController
