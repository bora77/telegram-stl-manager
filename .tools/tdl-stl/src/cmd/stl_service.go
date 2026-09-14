package cmd

import (
	"context"
	"flag"
	"fmt"
	"io"
	"path/filepath"
	"time"

	"github.com/gotd/td/telegram"
	"github.com/iyear/tdl/app/chat"
	"github.com/iyear/tdl/app/stl"
	"github.com/iyear/tdl/app/stlservice"
	"github.com/iyear/tdl/core/stltransport"
	"github.com/iyear/tdl/core/storage"
	"github.com/iyear/tdl/pkg/consts"
	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
	"github.com/spf13/viper"
)

func serviceSocket() string {
	return filepath.Join(filepath.Dir(viper.GetStringMapString(consts.FlagStorage)["path"]), "service.sock")
}

func proxySTL(cmd *cobra.Command, source string) (bool, error) {
	source, _ = filepath.Abs(source)
	request := stlservice.Request{Source: source, Args: []string{cmd.Name()}}
	cmd.LocalNonPersistentFlags().VisitAll(func(f *pflag.Flag) {
		if f.Changed {
			request.Args = append(request.Args, "--"+f.Name+"="+f.Value.String())
		}
	})
	return stlservice.Call(cmd.Context(), serviceSocket(), request)
}

func newSTLService(source *string) *cobra.Command {
	return &cobra.Command{Use: "serve", Short: "Serve independent local jobs using one Telegram login", Args: cobra.NoArgs, RunE: func(cmd *cobra.Command, args []string) error {
		absolute, err := filepath.Abs(*source)
		if err != nil {
			return err
		}
		if _, err := stl.LoadSource(absolute); err != nil {
			return err
		}
		resolver := &stltransport.Resolver{}
		return tRun(stltransport.With(cmd.Context(), resolver), func(ctx context.Context, c *telegram.Client, kv storage.Storage) error {
			return stlservice.Serve(ctx, serviceSocket(), absolute, 60*time.Second, func(ctx context.Context, request stlservice.Request) error {
				return runSTLRequest(ctx, c, kv, resolver, request)
			})
		}, limiter)
	}}
}

func runSTLRequest(ctx context.Context, c *telegram.Client, kv storage.Storage, resolver *stltransport.Resolver, request stlservice.Request) error {
	if len(request.Args) == 0 {
		return fmt.Errorf("missing Telegram operation")
	}
	source, err := stl.LoadSource(request.Source)
	if err != nil {
		return err
	}
	f := flag.NewFlagSet(request.Args[0], flag.ContinueOnError)
	f.SetOutput(io.Discard)
	var output, events string
	var topic, after int
	var o stl.Options
	switch request.Args[0] {
	case "servers":
		f.StringVar(&output, "output", "", "")
	case "files":
		f.IntVar(&topic, "topic", 0, "")
		f.IntVar(&after, "after-id", 0, "")
		f.StringVar(&output, "output", "", "")
	case "download":
		f.IntVar(&o.Topic, "topic", 0, "")
		f.IntVar(&o.Message, "message", 0, "")
		f.IntVar(&o.DC, "dc", 0, "")
		f.Int64Var(&o.DocumentID, "document-id", 0, "")
		f.Int64Var(&o.Size, "size", 0, "")
		f.StringVar(&o.Filename, "filename", "", "")
		f.StringVar(&o.Output, "output", "", "")
		f.StringVar(&events, "events", "", "")
		f.StringVar(&o.Server, "server", "auto", "")
		f.StringVar(&o.Cache, "cache", "", "")
		f.StringVar(&o.Network, "network", "default", "")
		f.BoolVar(&o.IPv6, "ipv6", false, "")
		f.BoolVar(&o.Retest, "retest", false, "")
		f.Float64Var(&o.MinSpeedMBPS, "min-speed-mbps", 0, "")
		f.BoolVar(&o.QuickTest, "quick-test", false, "")
		f.StringVar(&o.ReuseServer, "reuse-server", "", "")
		f.BoolVar(&o.ProbeOnly, "probe-only", false, "")
	default:
		return fmt.Errorf("unknown Telegram operation")
	}
	if err = f.Parse(request.Args[1:]); err != nil {
		return err
	}
	if f.NArg() != 0 {
		return fmt.Errorf("unexpected Telegram arguments")
	}
	switch request.Args[0] {
	case "servers":
		if output == "" {
			return fmt.Errorf("missing server output")
		}
		return stl.WriteJSON(output, map[string]any{"endpoints": stl.Servers(c)})
	case "files":
		if topic <= 0 || after < 0 || after >= 2147483647 || output == "" {
			return fmt.Errorf("invalid topic scan")
		}
		last := after
		if err = chat.Export(ctx, c, kv, chat.ExportOptions{Type: chat.ExportTypeId, Chat: fmt.Sprint(source.ChatID), Thread: topic, Input: []int{after + 1, 2147483646}, Output: output, Filter: "true", Raw: true, AfterID: after, LastID: &last}); err != nil {
			return err
		}
		if err = ctx.Err(); err != nil {
			return err
		}
		if err = stl.WriteJSON(output+".cursor", map[string]any{"after": after, "last_id": last, "topic": topic}); err != nil {
			return err
		}
		return stl.WriteJSON(output+".complete", map[string]any{"complete": true, "topic": topic})
	case "download":
		o.ChatID = source.ChatID
		e, err := stl.OpenEvents(events)
		if err != nil {
			return err
		}
		defer e.Close()
		err = stl.Download(ctx, c, kv, resolver, o, e)
		if err != nil {
			e.Emit("error", map[string]any{"message": err.Error()})
		}
		return err
	}
	return nil
}
