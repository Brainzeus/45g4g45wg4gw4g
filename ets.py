
    runner = web.AppRunner(api.app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8080)
    
    await asyncio.gather(
        node.start(),
        site.start()
    )

if __name__ == "__main__":
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    asyncio.run(main())
