return function(H)
	H.test("harness resolves script.Parent.X and script.Parent.Parent.X", function()
		local EquipmentDefs = H.load("shared/Combat/EquipmentDefs")
		H.expect(EquipmentDefs.Items.w_pistol ~= nil, "EquipmentDefs loads via script.Parent.Parent")
		local RunnerSim = H.load("shared/Runner/RunnerSim")
		H.expect(type(RunnerSim.Resolve) == "function", "RunnerSim loads")
	end)
end
