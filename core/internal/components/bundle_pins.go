package components

// The 1.2 release line pins the eight verified Windows x64 partner bundles.
// Both supported suffixes use this same byte format and trusted whole-file hash.
type bundlePin struct {
	version, release, hash string
	bytes, componentBytes  int64
}

var bundlePins = map[string]bundlePin{
	"codex":    {"0.172.0", "codex-0.172.0-windows-amd64-e80bd26204520581", "1df81028ab25b0a089cd8bef04106cf42a457d63e9a2902a26453690664db625", 189582884, 548562559},
	"deepseek": {"0.2.2-rc.5", "deepseek-0.2.2-rc.5-windows-amd64-c4b343ff0602332a", "cc70045a07fb8689b20f26b71752b67ef83e4278c5a7d070c284b9a069856d65", 340504281, 1201489088},
	"qwen":     {"0.28.4", "qwen-0.28.4-windows-amd64-2a195c80308ee3de", "6b83e15731d29fb3e01f6b2632de1481671c955a00c8d9f3d7bf7bf84ca64f10", 96667719, 215246747},
	"claude":   {"2.1.332", "claude-2.1.332-windows-amd64-081538ebd1276b11", "52cc8a334e23cca19cc00616eec9ce66cc879fd74a73c9a6bf08e6c777565839", 184387269, 403083994},
	"grok":     {"1.0.92", "grok-1.0.92-windows-amd64-366e4ad2078f11e9", "eb8eedb8acce7b1611b3f9759b8f8c1f3c9240fc78e66c96798e43b2783f4d41", 99550594, 245391936},
	"gemini":   {"0.71.1", "gemini-0.71.1-windows-amd64-b5f52522c14fdd9d", "ea296dfb2f3d7ee40fb92b4d45547803bbdefde7d777e4272169cb9202f43c34", 62285489, 134995889},
	"kimi":     {"2.8.0", "kimi-2.8.0-windows-amd64-94a3c94be41b0d78", "b2009981a60e0c4a1a5bb9ecbfb6aaa5414962f5c9524ca29ea9087c62a5185d", 56155963, 163147040},
	"glm":      {"4.0.0", "glm-4.0.0-windows-amd64-be5b481588eb2cc6", "5fe58fc7ce6643ce28e430a7fbf3f7247f14fe584840a96fddd8be442d3f8afc", 56541263, 76789396},
}
